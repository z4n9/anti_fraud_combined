"""Risk engine: решение по порогам + эвристические risk indicators.

Индикаторы (supporting signals) считаются по признакам независимо от ML-модели и НЕ
являются точным объяснением того, почему LightGBM выдал данный score.
Пороги и параметры индикаторов - в config/risk_config.yaml, меняются без переобучения.
"""
import math
import os
import threading
from pathlib import Path

import yaml

INDICATOR_KEYS = ("amount_above_sender_history_ratio", "high_velocity_tx_1h",
                  "many_distinct_recipients_24h", "high_amount", "repeated_to_recipient_24h")


class RiskConfigError(ValueError):
    pass


def _isnan(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


class RiskEngine:
    def __init__(self, config_path):
        self.path = Path(config_path)
        self._mtime = None
        self._lock = threading.Lock()
        self.cfg = None
        self._load()

    # ---- конфиг с hot-reload по mtime
    def _load(self):
        cfg = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        th = cfg.get("thresholds", {})
        review, high = th.get("review"), th.get("high_risk")
        if not (isinstance(review, (int, float)) and isinstance(high, (int, float))
                and 0 < review < high <= 1):
            raise RiskConfigError(
                f"некорректные пороги: нужно 0 < review < high_risk <= 1, получено {th}")
        for k in INDICATOR_KEYS:
            if k not in cfg.get("indicators", {}):
                raise RiskConfigError(f"в indicators нет параметра {k}")
        self._validate_rules(cfg)
        self.cfg = cfg
        self._mtime = os.stat(self.path).st_mtime_ns

    @staticmethod
    def _validate_rules(cfg):
        """Секции STEP 9. Отсутствие/ошибка = исключение: молча отключать защитные
        правила из-за опечатки в конфиге нельзя."""
        def need(d, k, where):
            if not isinstance(d, dict) or k not in d:
                raise RiskConfigError(f"в {where} нет параметра {k}")
            return d[k]
        def pos(x, name):
            if not isinstance(x, (int, float)) or isinstance(x, bool) or x <= 0:
                raise RiskConfigError(f"{name} должно быть положительным числом, получено {x!r}")
        rules = need(cfg, "rules", "конфиге")
        cs = need(rules, "cold_start", "rules")
        a, b = need(cs, "review_amount", "rules.cold_start"), need(cs, "high_risk_amount", "rules.cold_start")
        pos(a, "cold_start.review_amount"); pos(b, "cold_start.high_risk_amount")
        if a > b:
            raise RiskConfigError("cold_start.review_amount не должен превышать high_risk_amount")
        vel = need(rules, "velocity", "rules")
        if not need(vel, "count_statuses", "rules.velocity"):
            raise RiskConfigError("rules.velocity.count_statuses не может быть пустым")
        for k in ("transfers_1h", "unique_receivers_1h", "amount_sum_1h", "same_recipient_1h"):
            th = need(vel, k, "rules.velocity")
            r_, h_ = need(th, "review", f"rules.velocity.{k}"), need(th, "high_risk", f"rules.velocity.{k}")
            pos(r_, f"{k}.review"); pos(h_, f"{k}.high_risk")
            if r_ > h_:
                raise RiskConfigError(f"{k}: review не должен превышать high_risk")
        ood = need(rules, "out_of_distribution", "rules")
        pos(need(ood, "amount_spike_ratio", "rules.out_of_distribution"), "amount_spike_ratio")
        need(ood, "review_with_other_factors", "rules.out_of_distribution")
        pos(need(need(rules, "escalation", "rules"), "min_independent_families_for_high_risk",
                 "rules.escalation"), "min_independent_families_for_high_risk")
        # семейства правил: два срабатывания одного семейства - не две независимые причины
        fams = need(rules, "families", "rules")
        for rule in ("cold_start_new_recipient_large_amount", "velocity_transfers_1h",
                     "velocity_unique_receivers_1h", "velocity_amount_sum_1h",
                     "repeated_transfers_same_recipient_1h",
                     "out_of_distribution_with_risk_factors"):
            if not isinstance(fams.get(rule), str) or not fams[rule]:
                raise RiskConfigError(f"rules.families: не задано семейство для {rule}")
        mt = need(vel["amount_sum_1h"], "min_transfers", "rules.velocity.amount_sum_1h")
        if not isinstance(mt, int) or isinstance(mt, bool) or mt < 2:
            raise RiskConfigError("amount_sum_1h.min_transfers: целое >= 2 (сумма за час - "
                                  "про накопление, а не про один крупный перевод)")
        if not isinstance(need(cfg, "production_ready", "конфиге"), bool):
            raise RiskConfigError("production_ready должно быть true/false")
        cur = need(cfg, "currency", "конфиге")
        sup = need(cur, "supported", "currency")
        if not sup or not all(isinstance(c, str) and len(c) == 3 and c.isupper() for c in sup):
            raise RiskConfigError(f"currency.supported: коды ISO 4217 в верхнем регистре, получено {sup!r}")
        if need(cur, "assume_if_missing", "currency") not in sup:
            raise RiskConfigError("currency.assume_if_missing должна входить в currency.supported")
        if not isinstance(need(need(cfg, "lifecycle", "конфиге"), "auto_complete_allow", "lifecycle"), bool):
            raise RiskConfigError("lifecycle.auto_complete_allow должно быть true/false")
        known = {"REQUESTED", "PENDING_REVIEW", "REJECTED", "COMPLETED"}
        for where, lst in (("history.count_statuses", cfg.get("history", {}).get("count_statuses")),
                           ("rules.velocity.count_statuses", vel["count_statuses"])):
            if not lst or not set(lst) <= known:
                raise RiskConfigError(f"{where}: допустимы только {sorted(known)}, получено {lst!r}")

    def refresh(self):
        """Перечитать конфиг, если файл изменился. Невалидный конфиг не ломает работающий
        сервис: остаётся предыдущая валидная конфигурация, ошибка пробрасывается."""
        with self._lock:
            m = os.stat(self.path).st_mtime_ns
            if m != self._mtime:
                self._mtime = m  # не перечитывать тот же файл повторно
                self._load()

    @property
    def thresholds(self):
        return dict(self.cfg["thresholds"])

    @property
    def rules_cfg(self):
        return self.cfg["rules"]

    @property
    def currency_cfg(self):
        return self.cfg["currency"]

    @property
    def auto_complete_allow(self):
        return self.cfg["lifecycle"]["auto_complete_allow"]

    @property
    def velocity_statuses(self):
        return list(self.cfg["rules"]["velocity"]["count_statuses"])

    @property
    def count_statuses(self):
        return list(self.cfg.get("history", {}).get("count_statuses", []))

    # ---- решение
    def decide(self, score: float) -> str:
        th = self.cfg["thresholds"]
        if score >= th["high_risk"]:
            return "HIGH_RISK"
        if score >= th["review"]:
            return "REVIEW"
        return "ALLOW"

    # ---- supporting signals
    def indicators(self, f: dict) -> list:
        c = self.cfg["indicators"]
        out = []
        if f.get("is_new_recipient") == 1:
            out.append("new_recipient")
        if _isnan(f.get("time_since_last_transaction")):
            out.append("first_transaction_for_sender")
        r = f.get("amount_vs_sender_avg")
        if not _isnan(r) and r >= c["amount_above_sender_history_ratio"]:
            out.append("amount_above_sender_history")
        if f.get("sender_tx_count_1h", 0) >= c["high_velocity_tx_1h"]:
            out.append("high_transaction_velocity")
        if f.get("sender_unique_receivers_24h", 0) >= c["many_distinct_recipients_24h"]:
            out.append("many_distinct_recipients")
        if f.get("amount", 0) >= c["high_amount"]:
            out.append("high_amount")
        if f.get("transactions_to_same_receiver_24h", 0) >= c["repeated_to_recipient_24h"]:
            out.append("repeated_transfers_to_recipient")
        return out
