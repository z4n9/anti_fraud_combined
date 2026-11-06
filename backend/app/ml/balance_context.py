"""Private server-balance context, separate from the trained 21-feature vector.

The 80% guard runs on a model REVIEW or in the bank's rule-only fallback.
Integer comparisons keep the boundary exact in tiyn.
"""
from decimal import Decimal
import math

CONTEXT_VERSION = "sender-balance-v1"
BALANCE_RULE = "amount_ge_80pct_balance"
BALANCE_THRESHOLD = 0.8


def build_balance_context(*, amount_cents, sender_balance_cents):
    if sender_balance_cents is None:
        return None
    if (isinstance(sender_balance_cents, bool) or not isinstance(sender_balance_cents, int)
            or sender_balance_cents < 0):
        raise ValueError("Sender balance must be nonnegative integer tiyn")
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int) or amount_cents <= 0:
        raise ValueError("Transfer amount must be positive integer tiyn")
    ratio = (float(Decimal(amount_cents) / Decimal(sender_balance_cents))
             if sender_balance_cents else None)
    if ratio is not None and not math.isfinite(ratio):
        raise ValueError("Amount-to-balance ratio must be finite")
    return {"balance_before_cents": sender_balance_cents, "amount_cents": amount_cents,
            "projected_balance_after_cents": sender_balance_cents - amount_cents,
            "amount_to_balance_ratio": ratio, "context_version": CONTEXT_VERSION,
            "rule_threshold": BALANCE_THRESHOLD}


def balance_rule(context, *, evaluate=True):
    evaluated = evaluate and context is not None
    fired = bool(evaluated and context["balance_before_cents"] > 0
                 and context["amount_cents"] * 100 >= context["balance_before_cents"] * 80)
    return {"rule": BALANCE_RULE, "family": "balance", "evaluated": evaluated, "triggered": fired,
            "level": "REVIEW" if fired else "ALLOW"}


def attach_balance_context(result, context, *, evaluate_rule):
    if context is None:
        return result
    return {**result, "balance_context": context, "balance_rule": balance_rule(context, evaluate=evaluate_rule)}
