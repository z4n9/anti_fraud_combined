"""Защитные правила банковской безопасности (rule_engine), версия STEP 10.

Работает НЕЗАВИСИМО от ML: не читает и не меняет ml_score. Итоговое решение формирует
risk_engine как более строгий из ml_decision и rule_decision. Все пороги - в YAML,
демонстрационные и откалиброванные на train/validation синтетического датасета (не
банковские нормативы).

triggered_rules - только реально СРАБОТАВШИЕ правила. Каждое правило принадлежит семейству
(cold_start / velocity / recipient / ood). Два правила одного семейства - не две независимые
причины: HIGH_RISK выдаётся только если
  * сработало правило на собственном HIGH_RISK-уровне ("очень сильное отдельное правило"), или
  * сработали правила минимум ДВУХ независимых семейств (OOD-правило не считается:
    это флаг ненадёжности модели, а не самостоятельный риск).
Правило суммы за час требует и накопления (>= min_transfers попыток): один крупный
перевод его не запускает.
"""
import math
from dataclasses import dataclass, field

ORDER = {"ALLOW": 0, "REVIEW": 1, "HIGH_RISK": 2}
RULES = ("cold_start_new_recipient_large_amount", "velocity_transfers_1h",
         "velocity_unique_receivers_1h", "velocity_amount_sum_1h",
         "repeated_transfers_same_recipient_1h", "out_of_distribution_with_risk_factors")


def stricter(a: str, b: str) -> str:
    return a if ORDER[a] >= ORDER[b] else b


def _nan(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


@dataclass
class RuleResult:
    rule_decision: str = "ALLOW"
    triggered_rules: list = field(default_factory=list)
    rule_details: list = field(default_factory=list)
    factors: list = field(default_factory=list)      # все выявленные факторы (диагностика)
    families: list = field(default_factory=list)     # семейства сработавших правил


class RuleEngine:
    def __init__(self, cfg_getter):
        """cfg_getter: callable -> актуальный dict секции rules (поддерживает hot-reload)."""
        self._cfg = cfg_getter

    @property
    def cfg(self):
        return self._cfg()

    # ------------------------------------------------------------------
    def evaluate(self, f: dict, velocity: dict, ood) -> RuleResult:
        """f: признаки модели; velocity: transfers_1h, unique_receivers_1h, amount_sum_1h,
        same_recipient_1h (по попыткам, включая текущую); ood: OODResult."""
        cfg = self.cfg
        out = RuleResult()
        if not cfg.get("enabled", True):
            return out
        fam = cfg["families"]

        # ---- факторы
        is_new = f.get("is_new_recipient") == 1
        never = _nan(f.get("time_since_last_transaction"))     # проведённых переводов не было
        no_30d = _nan(f.get("sender_avg_amount_30d"))          # нет проведённых за 30 дней
        cold = never
        dormant = (not never) and no_30d
        history_absent = cold or dormant
        ratio = f.get("amount_vs_sender_avg")
        spike = (not _nan(ratio)) and ratio >= cfg["out_of_distribution"]["amount_spike_ratio"]
        if cold:
            out.factors.append("cold_start")
        if dormant:
            out.factors.append("dormant_sender")
        if is_new:
            out.factors.append("new_recipient")
        if spike:
            out.factors.append("amount_above_sender_history")

        def add(rule, level, factors, **values):
            out.triggered_rules.append(rule)
            out.rule_details.append({"rule": rule, "family": fam[rule], "level": level,
                                     "factors": factors, "values": values})

        # ---- cold start / спящий клиент + новый получатель + крупная сумма
        cs = cfg["cold_start"]
        amount = f["amount"]
        if history_absent and is_new and amount >= cs["review_amount"]:
            level = "HIGH_RISK" if amount >= cs["high_risk_amount"] else "REVIEW"
            add("cold_start_new_recipient_large_amount", level,
                ["cold_start" if cold else "dormant_sender", "new_recipient", "high_amount"],
                amount=amount, review_amount=cs["review_amount"],
                high_risk_amount=cs["high_risk_amount"])

        # ---- velocity за час (по попыткам, включая текущую)
        vel = cfg["velocity"]
        vel_fired = 0
        for rule, key in (("velocity_transfers_1h", "transfers_1h"),
                          ("velocity_unique_receivers_1h", "unique_receivers_1h"),
                          ("velocity_amount_sum_1h", "amount_sum_1h"),
                          ("repeated_transfers_same_recipient_1h", "same_recipient_1h")):
            th = vel[key]
            v = velocity[key]
            if key == "amount_sum_1h" and velocity["transfers_1h"] < th["min_transfers"]:
                continue        # сумма - про накопление: нужен рост числа переводов
            if v >= th["high_risk"]:
                add(rule, "HIGH_RISK", [key], value=v, review=th["review"],
                    high_risk=th["high_risk"])
                vel_fired += 1
            elif v >= th["review"]:
                add(rule, "REVIEW", [key], value=v, review=th["review"],
                    high_risk=th["high_risk"])
                vel_fired += 1
        if vel_fired:
            out.factors.append("velocity")

        # ---- решение по правилам (без OOD): сильное отдельное правило или >= N семейств
        decision = "ALLOW"
        for d in out.rule_details:
            decision = stricter(decision, d["level"])          # уровень самого правила
        families = sorted({d["family"] for d in out.rule_details})
        if len(families) >= cfg["escalation"]["min_independent_families_for_high_risk"]:
            decision = "HIGH_RISK"                              # независимые причины
        out.families = families

        # ---- OOD: сам по себе не риск; вместе с другим фактором -> минимум REVIEW
        if ood.out_of_distribution:
            out.factors.append("out_of_distribution")
            # фактор не считается "другим", если это то же явление, что вызвало OOD
            same = {"out_of_distribution"}
            if any(r.startswith("time_since_last_transaction") for r in ood.reasons):
                same.add("dormant_sender")
            if any(r.startswith(("sender_tx_count", "sender_unique", "amount_sum",
                                 "transactions_to_same", "receiver_")) for r in ood.reasons):
                same.add("velocity")
            other = [x for x in out.factors if x not in same]
            if (cfg["out_of_distribution"]["review_with_other_factors"] and other
                    and ood.high_side):
                add("out_of_distribution_with_risk_factors", "REVIEW",
                    ["out_of_distribution"] + other, reasons=list(ood.reasons))
                decision = stricter(decision, "REVIEW")

        out.rule_decision = decision
        return out
