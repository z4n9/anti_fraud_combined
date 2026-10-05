"""Public explanations for deterministic signals; never publish graph identities."""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


LABELS = {
    "large_amount": "Необычно крупная сумма",
    "rapid_velocity": "Серия переводов за несколько минут",
    "daily_velocity": "Много переводов за сутки",
    "structuring": "Дробление суммы на несколько переводов",
    "new_recipient": "Крупный перевод новому получателю",
    "night_activity": "Перевод в ночное время",
    "many_to_one": "Много отправителей у получателя",
    "short_cycle": "Замкнутая цепочка переводов",
    "rapid_cashout": "Быстрый вывод поступивших средств",
    "dormant_account": "Возобновление активности после перерыва",
    "behavior_deviation": "Несколько необычных признаков одновременно",
    "balance_share": "Перевод значительной части баланса",
}


def public_label(code: str) -> str:
    return LABELS.get(code, "Обнаружено отклонение в переводе")


def _number(evidence: Mapping[str, Any], key: str) -> str:
    try:
        value = float(evidence[key])
    except (KeyError, TypeError, ValueError, OverflowError):
        return "не определено"
    if not math.isfinite(value):
        return "не определено"
    return f"{value:,.2f}".rstrip("0").rstrip(".").replace(",", " ")


def describe_signal(signal: Mapping[str, Any]) -> str:
    """Render a whitelist of numeric facts, excluding paths, accounts and raw JSON."""
    code = str(signal.get("code", ""))
    evidence = signal.get("evidence", {})
    if not isinstance(evidence, Mapping):
        evidence = {}
    number = lambda key: _number(evidence, key)
    if code == "large_amount":
        return f"Сумма {number('amount')} ₸ значительно превышает сумму сравнения {number('baseline_median')} ₸."
    if code == "rapid_velocity":
        return f"С учётом запланированного перевода — {number('transactions_5m')} операций за 5 минут."
    if code == "daily_velocity":
        return f"С учётом запланированного перевода — {number('transactions_1d')} операций за сутки."
    if code == "structuring":
        return f"За 30 минут — {number('transactions_30m')} переводов на общую сумму {number('total_30m')} ₸."
    if code == "new_recipient":
        return f"Получатель новый; сумма в {number('amount_to_client_median')} раза больше суммы сравнения."
    if code == "night_activity":
        return "Операция запланирована в ночное время и отличается от обычной активности."
    if code == "many_to_one":
        return f"У получателя много отправителей за последний час: {number('unique_senders_1h')}."
    if code == "short_cycle":
        return "Запланированный перевод замыкает короткую цепочку движения средств."
    if code == "rapid_cashout":
        return f"За последние {number('window_minutes')} минут поступило {number('inbound_amount')} ₸; значительная часть переводится дальше."
    if code == "dormant_account":
        key = "inactive_days" if "inactive_days" in evidence else "history_span_days"
        return f"В доступной истории обнаружен перерыв активности: {number(key)} дней."
    if code == "behavior_deviation":
        return "Одновременно изменились несколько признаков операции: сумма, время или доступный контекст."
    if code == "balance_share":
        return "Запланированный перевод составляет значительную часть доступного баланса."
    return "Обнаружен сигнал риска. Он требует проверки и не доказывает мошенничество."
