"""Embedded, optional SynSEPA inference with pinned assets and no writable store."""
from datetime import timedelta, timezone
import hashlib
import math
import os
from pathlib import Path
import threading

from .manifest import ASSET_SHA256
from .adapt_units import MODEL_ID, MODEL_VERSION, UNIT_VERSION
from .balance_context import attach_balance_context, build_balance_context

ASSET_DIR = Path(__file__).resolve().parent / "assets"
LOCAL_TIMEZONE = timezone(timedelta(hours=5))
_CACHE = None
_CACHE_LOCK = threading.Lock()
_PREDICT_LOCK = threading.Lock()


class MLUnavailable(RuntimeError):
    def __init__(self, reason, *, diagnostics=None):
        self.reason = reason
        self.diagnostics = diagnostics or {}
        super().__init__(reason)


def _mode():
    return os.environ.get("AMAN_ML_MODE", "auto").strip().lower()


def _units():
    return {"currency": "KZT", "model_currency": "KZT", "unit_version": UNIT_VERSION}


def _asset_dir():
    """An operator override still has to match every pinned asset checksum."""
    return Path(os.environ.get("AMAN_ML_ASSET_DIR", str(ASSET_DIR)))


def _checksums():
    actual = {}
    for name, expected in ASSET_SHA256.items():
        try:
            actual[name] = hashlib.sha256((_asset_dir() / name).read_bytes()).hexdigest()
        except OSError as exc:
            raise MLUnavailable("model_asset_missing", diagnostics={"asset": name}) from exc
        if actual[name] != expected:
            raise MLUnavailable("model_asset_checksum_mismatch", diagnostics={"asset": name})
    return actual


def _bundle(checksums):
    global _CACHE
    asset_dir = _asset_dir()
    key = (str(asset_dir.resolve()), tuple(checksums.items()))
    with _CACHE_LOCK:
        if _CACHE is None or _CACHE[0] != key:
            # Optional numerical dependencies are never imported for mode=off.
            from .model_service import ModelService
            from .ood import OODChecker
            from .risk_engine import RiskEngine
            from .rule_engine import RuleEngine

            model = ModelService(asset_dir / "S0_CLEAN_final.txt", asset_dir / "S0_CLEAN_final.meta.json")
            if (model.model_id != MODEL_ID or model.model_version != MODEL_VERSION
                    or model.meta.get("currency") != "KZT" or model.meta.get("unit_version") != UNIT_VERSION):
                raise MLUnavailable("model_unit_contract_mismatch")
            risk = RiskEngine(asset_dir / "risk_config.yaml")
            ood = OODChecker(asset_dir / "S0_CLEAN_final.ood.json")
            rules = RuleEngine(lambda: risk.rules_cfg)
            _CACHE = (key, model, risk, rules, ood)
        return _CACHE[1:]


def _failure(exc, mode):
    reason = exc.reason if isinstance(exc, MLUnavailable) else (
        "ml_dependency_unavailable" if isinstance(exc, ImportError) else "ml_inference_unavailable")
    result = {**_units(), "status": "unavailable", "mode": mode, "model_id": MODEL_ID,
              "model_version": MODEL_VERSION, "reason": reason}
    if isinstance(exc, MLUnavailable):
        result.update(exc.diagnostics)
    if mode not in {"auto", "off"}:
        raise MLUnavailable(reason, diagnostics=result) from exc
    return result


def get_ml_status():
    """Operational diagnostics only: no prediction and no filesystem paths."""
    mode = _mode()
    if mode == "off":
        return {**_units(), "status": "disabled", "mode": mode, "model_id": MODEL_ID, "model_version": MODEL_VERSION}
    try:
        if mode not in {"auto", "required"}:
            raise MLUnavailable("invalid_ml_mode")
        checksums = _checksums()
        model, risk, _, _ = _bundle(checksums)
        return {**_units(), "status": "available", "mode": mode, "model_id": model.model_id,
                "model_version": model.model_version, "thresholds": risk.thresholds,
                "checksums": checksums,
                "monetary_thresholds_kzt": {
                    "cold_start_review": risk.rules_cfg["cold_start"]["review_amount"],
                    "cold_start_high_risk": risk.rules_cfg["cold_start"]["high_risk_amount"],
                    "velocity_amount_sum_1h_review": risk.rules_cfg["velocity"]["amount_sum_1h"]["review"],
                    "velocity_amount_sum_1h_high_risk": risk.rules_cfg["velocity"]["amount_sum_1h"]["high_risk"],
                    "high_amount_indicator": risk.cfg["indicators"]["high_amount"]},
                "feature_count": len(model.feature_names), "production_ready": False}
    except Exception as exc:
        return _failure(exc, mode)


def evaluate_synsepa(history, *, sender_id, receiver_id, amount_cents, evaluated_at,
                     sender_balance_cents: int | None = None):
    """Score a candidate using only preceding supplied history; never mutate it.

    Model NaNs remain NaN during prediction and become null in returned audit
    features. Monetary features and model thresholds are both native KZT.
    """
    try:
        context = build_balance_context(amount_cents=amount_cents, sender_balance_cents=sender_balance_cents)
    except (ValueError, ArithmeticError) as exc:
        # Invalid authoritative context must never be silently ignored, even
        # when ML is off or the ordinary auto fallback would remain available.
        raise MLUnavailable("invalid_balance_context") from exc
    try:
        status = get_ml_status()
    except MLUnavailable as exc:
        exc.diagnostics = attach_balance_context(exc.diagnostics, context, evaluate_rule=False)
        raise
    if status["status"] != "available":
        return attach_balance_context(status, context, evaluate_rule=True)
    try:
        from .history import MemoryHistory, aware_timestamp, kzt_amount
        from .online_features import OnlineFeatureBuilder, Tx
        from .rule_engine import stricter

        aware_timestamp(evaluated_at)
        if not isinstance(sender_id, str) or not sender_id or not isinstance(receiver_id, str) or not receiver_id:
            raise ValueError("ML identities must be nonempty strings")
        amount = kzt_amount(amount_cents)
        store = MemoryHistory(history, evaluated_at=evaluated_at)
        model, risk, rules, ood = _bundle(status["checksums"])
        store.count_statuses = risk.count_statuses
        builder = OnlineFeatureBuilder(store, model.feature_names)
        tx = Tx(transaction_id="candidate", sender_id=sender_id, receiver_id=receiver_id,
                amount=amount, timestamp=evaluated_at.astimezone(LOCAL_TIMEZONE),
                sender_balance=context["balance_before_cents"] / 100 if context is not None else None)
        features = builder.build(tx)
        with _PREDICT_LOCK:
            score = model.predict(features)
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise MLUnavailable("invalid_model_score")
        ml_decision = risk.decide(score)
        velocity = builder.velocity(tx, risk.velocity_statuses)
        ood_result = ood.check(features)
        rule_result = rules.evaluate(features, velocity, ood_result)
        result = {**status, "score": score, "ml_decision": ml_decision,
                "rule_decision": rule_result.rule_decision,
                "decision": stricter(ml_decision, rule_result.rule_decision),
                "out_of_distribution": ood_result.out_of_distribution, "ood_reasons": ood_result.reasons,
                "features": {key: value if math.isfinite(value) else None for key, value in features.items()},
                "velocity": velocity, "triggered_rules": rule_result.triggered_rules,
                "rule_details": rule_result.rule_details, "risk_indicators": risk.indicators(features),
                "rule_factors": rule_result.factors, "rule_families": rule_result.families,
                "amount_kzt": amount, "history_count": len(store.rows),
                "model_history_count": sum(row.status in store.count_statuses for row in store.rows),
                "evaluated_at": evaluated_at.isoformat()}
        return attach_balance_context(result, context, evaluate_rule=ml_decision == "REVIEW")
    except Exception as exc:
        try:
            result = _failure(exc, status["mode"])
        except MLUnavailable as failure:
            failure.diagnostics = attach_balance_context(failure.diagnostics, context, evaluate_rule=False)
            raise
        return attach_balance_context(result, context, evaluate_rule=True)
