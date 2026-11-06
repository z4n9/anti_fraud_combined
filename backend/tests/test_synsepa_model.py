"""Real-model source parity and failure isolation for embedded SynSEPA inference."""
import builtins
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys

import pytest

from app.ml import MLUnavailable, evaluate_synsepa, get_ml_status
from app.ml import service
from app.ml.history import MemoryHistory, kzt_amount
from app.ml.adapt_units import MONETARY_FEATURES, UNIT_SCALE
from app.ml.manifest import ASSET_SHA256

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone(timedelta(hours=5)))
SOURCE = Path(__file__).resolve().parents[3] / "anti_fraud_1" / "antifraud-runtime"


@pytest.fixture(autouse=True)
def enable_ml(monkeypatch):
    monkeypatch.setenv("AMAN_ML_MODE", "auto")
    monkeypatch.setenv("OMP_NUM_THREADS", "1")
    monkeypatch.delenv("AMAN_ML_ASSET_DIR", raising=False)


def row(identifier, *, sender="sender", receiver="known", minutes=30, cents=10_000_000, status="COMPLETED"):
    return {"id": identifier, "sender_id": sender, "receiver_id": receiver,
            "amount_cents": cents, "timestamp": NOW - timedelta(minutes=minutes), "status": status}


def evaluate(history=(), **kwargs):
    return evaluate_synsepa(list(history), sender_id="sender", receiver_id=kwargs.pop("receiver_id", "new"),
                            amount_cents=kwargs.pop("amount_cents", 110_000_000),
                            evaluated_at=kwargs.pop("evaluated_at", NOW), **kwargs)


def original_module(name, relative):
    path = SOURCE / relative
    if not path.exists():
        pytest.skip("Source repository is needed only for independent parity tests")
    spec = importlib.util.spec_from_file_location("synsepa_original_" + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def original():
    pytest.importorskip("lightgbm")
    return {name: original_module(name, path) for name, path in {
        "features": "src/online_features.py", "model": "api/model_service.py",
        "risk": "api/risk_engine.py", "rules": "api/rule_engine.py", "ood": "api/ood.py"}.items()}


@pytest.mark.parametrize("history,receiver,amount", [
    ([], "new", 110_000_000),
    ([row("old", minutes=120), row("recent", minutes=30)], "known", 5_000_000),
    ([row(str(i), receiver=str(i), minutes=5 + i, status="REJECTED") for i in range(5)], "new", 300_000_000),
])
def test_real_model_features_rules_ood_and_score_match_original(original, history, receiver, amount, tmp_path):
    # Windows checkout may translate source LF to CRLF but tree_sizes are byte
    # offsets for LF. Normalize a temporary reference copy without editing source.
    normalized = tmp_path / "source-model-lf.txt"
    normalized.write_text((SOURCE / "models/S0_CLEAN_final.txt").read_text(encoding="utf-8"),
                          encoding="utf-8", newline="\n")
    model = original["model"].ModelService(normalized,
                                             SOURCE / "models/S0_CLEAN_final.meta.json")
    model.booster.params["num_threads"] = 1
    risk = original["risk"].RiskEngine(SOURCE / "config/risk_config.yaml")
    store = original["features"].HistoryStore(":memory:")  # Source-only feature reference, never bank money.
    try:
        store.count_statuses = risk.count_statuses
        for item in history:
            store.save(original["features"].Tx(str(item["id"]), item["sender_id"], item["receiver_id"],
                kzt_amount(item["amount_cents"]) / UNIT_SCALE, item["timestamp"]), item["status"], None)
        tx = original["features"].Tx("candidate", "sender", receiver, kzt_amount(amount) / UNIT_SCALE, NOW)
        builder = original["features"].OnlineFeatureBuilder(store, model.feature_names)
        features = builder.build(tx)
        score = model.predict(features)
        ood = original["ood"].OODChecker(SOURCE / "models/S0_CLEAN_final.ood.json").check(features)
        velocity = builder.velocity(tx, risk.velocity_statuses)
        rule = original["rules"].RuleEngine(lambda: risk.rules_cfg).evaluate(features, velocity, ood)
        result = evaluate(history, receiver_id=receiver, amount_cents=amount)
        assert result["status"] == "available", result
        assert list(result["features"]) == model.feature_names
        for key, value in features.items():
            if isinstance(value, float) and math.isnan(value):
                assert result["features"][key] is None
            else:
                assert result["features"][key] == pytest.approx(value * UNIT_SCALE if key in MONETARY_FEATURES else value)
        assert result["score"] == pytest.approx(score, abs=1e-14)
        assert result["ml_decision"] == risk.decide(score)
        assert result["rule_decision"] == rule.rule_decision
        assert result["decision"] == original["rules"].stricter(risk.decide(score), rule.rule_decision)
        assert result["velocity"] == {key: value * UNIT_SCALE if key == "amount_sum_1h" else value for key, value in velocity.items()}
        assert result["triggered_rules"] == rule.triggered_rules
        assert result["ood_reasons"] == ood.reasons
        assert result["out_of_distribution"] == ood.out_of_distribution
        assert result["thresholds"] == {"review": .3, "high_risk": .7}
        json.dumps(result, allow_nan=False)
    finally:
        store.close()


def test_strict_history_inclusive_lower_bound_statuses_timezone_and_no_mutation():
    history = [row("at-hour", minutes=60), row("outside-hour", minutes=60.0001),
               row("pending", minutes=10, status="PENDING_REVIEW"),
               row("rejected", minutes=5, status="REJECTED"), row("equal", minutes=0), row("future", minutes=-1)]
    before = deepcopy(history)
    result = evaluate(history)
    assert result["status"] == "available", result
    assert result["features"]["sender_tx_count_1h"] == 1
    assert result["features"]["sender_tx_count_24h"] == 2
    assert result["velocity"]["transfers_1h"] == 4  # Completed + two attempts + candidate.
    assert result["features"]["hour"] == 12
    assert result["features"]["day_of_week"] == 2
    assert result["history_count"] == 4 and result["model_history_count"] == 2
    equivalent = evaluate(history[:4], evaluated_at=NOW.astimezone(timezone.utc))
    assert equivalent["features"] == result["features"]
    assert equivalent["score"] == result["score"]
    assert history == before


def test_cold_start_nan_is_preserved_until_prediction_and_order_is_enforced(monkeypatch):
    from app.ml.online_features import OnlineFeatureBuilder, Tx
    status = get_ml_status()
    assert status["status"] == "available", status
    model, _, _, _ = service._bundle(status["checksums"])
    builder = OnlineFeatureBuilder(MemoryHistory([], evaluated_at=NOW), model.feature_names)
    features = builder.build(Tx("candidate", "sender", "new", 2200, NOW))
    assert math.isnan(features["sender_avg_amount_30d"])
    assert math.isnan(features["amount_vs_sender_avg"])
    captured = []
    predict = model.booster.predict

    def checked_predict(values, **kwargs):
        assert kwargs["num_threads"] == 1
        captured.append(values.copy())
        return predict(values, **kwargs)

    monkeypatch.setattr(model.booster, "predict", checked_predict)
    assert math.isfinite(model.predict(features))
    assert math.isnan(captured[0][0, model.feature_names.index("amount_vs_sender_avg")])
    with pytest.raises(ValueError, match="порядок"):
        model.predict(dict(reversed(list(features.items()))))


def test_native_assets_match_fixed_manifest():
    for name, expected in ASSET_SHA256.items():
        asset = service.ASSET_DIR / name
        assert hashlib.sha256(asset.read_bytes()).hexdigest() == expected
    status = get_ml_status()
    assert status["status"] == "available", status
    assert "score" not in status
    assert status["model_id"] == "synsepa_s0_clean_kzt"
    assert status["model_currency"] == "KZT" and status["unit_version"] == "kzt-native-v1"
    assert status["checksums"] == ASSET_SHA256
    assert ":\\" not in json.dumps(status)


def test_disabled_mode_never_imports_lightgbm_or_loads_assets(monkeypatch):
    monkeypatch.setenv("AMAN_ML_MODE", "off")
    monkeypatch.setenv("AMAN_ML_ASSET_DIR", "does-not-exist")
    original_import = builtins.__import__

    def import_without_ml(name, *args, **kwargs):
        assert name != "lightgbm"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_ml)
    assert get_ml_status()["status"] == "disabled"
    assert evaluate()["status"] == "disabled"


@pytest.mark.parametrize("asset_name", list(ASSET_SHA256))
def test_corrupt_asset_is_detected_even_after_model_cached(tmp_path, monkeypatch, asset_name):
    assert get_ml_status()["status"] == "available"
    directory = tmp_path / "assets"
    shutil.copytree(service.ASSET_DIR, directory)
    monkeypatch.setenv("AMAN_ML_ASSET_DIR", str(directory))
    assert get_ml_status()["status"] == "available"
    with (directory / asset_name).open("ab") as stream:
        stream.write(b"\ncorrupt")
    result = evaluate()
    assert result["status"] == "unavailable" and result["reason"] == "model_asset_checksum_mismatch"
    assert "score" not in result
    monkeypatch.setenv("AMAN_ML_MODE", "required")
    with pytest.raises(MLUnavailable, match="model_asset_checksum_mismatch"):
        evaluate()


def test_missing_asset_fallback_and_required_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("AMAN_ML_ASSET_DIR", str(tmp_path / "missing"))
    assert get_ml_status()["reason"] == "model_asset_missing"
    assert evaluate()["status"] == "unavailable"
    monkeypatch.setenv("AMAN_ML_MODE", "required")
    with pytest.raises(MLUnavailable, match="model_asset_missing"):
        get_ml_status()


def test_optional_import_failure_fallback_and_required(monkeypatch):
    monkeypatch.setattr(service, "_CACHE", None)
    original_import = builtins.__import__

    def missing_lightgbm(name, *args, **kwargs):
        if name == "lightgbm":
            raise ModuleNotFoundError("private operator path must not leak")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_lightgbm)
    assert evaluate()["reason"] == "ml_dependency_unavailable"
    monkeypatch.setenv("AMAN_ML_MODE", "required")
    with pytest.raises(MLUnavailable, match="ml_dependency_unavailable"):
        evaluate()


@pytest.mark.parametrize("score", [math.nan, math.inf, -0.1, 1.1])
def test_invalid_prediction_never_becomes_allow(monkeypatch, score):
    status = get_ml_status()
    model, _, _, _ = service._bundle(status["checksums"])
    monkeypatch.setattr(model, "predict", lambda features: score)
    result = evaluate()
    assert result["status"] == "unavailable" and "decision" not in result
    assert result["reason"] == "invalid_model_score"


def test_inference_exception_does_not_leak_path_or_allow_transfer(monkeypatch):
    status = get_ml_status()
    model, _, _, _ = service._bundle(status["checksums"])

    def failed(features):
        raise RuntimeError("C:\\private\\model-and-user-data")

    monkeypatch.setattr(model, "predict", failed)
    assert evaluate()["reason"] == "ml_inference_unavailable"
    assert "private" not in json.dumps(evaluate())
    monkeypatch.setenv("AMAN_ML_MODE", "required")
    with pytest.raises(MLUnavailable, match="ml_inference_unavailable"):
        evaluate()


def test_native_kzt_amount_and_invalid_inputs(monkeypatch):
    result = evaluate(amount_cents=60_000_001)
    assert result["amount_kzt"] == 600000.01
    assert result["features"]["amount"] == 600000.01
    assert result["currency"] == result["model_currency"] == "KZT"
    assert not {"amount_eur", "kzt_per_eur", "demo_kzt_per_eur", "exchange_rate_is_demo"}.intersection(result)
    assert evaluate(evaluated_at=NOW.replace(tzinfo=None))["status"] == "unavailable"
    for invalid in (True, 0, -1, 1.5, "100", 10 ** 1000):
        assert evaluate(amount_cents=invalid)["status"] == "unavailable"
    assert evaluate([row("same"), row("same")])["status"] == "unavailable"
    monkeypatch.setenv("AMAN_ML_MODE", "required")
    with pytest.raises(MLUnavailable):
        evaluate([row("same"), row("same")])


def test_unknown_ml_mode_is_a_configuration_failure(monkeypatch):
    monkeypatch.setenv("AMAN_ML_MODE", "requiredd")
    with pytest.raises(MLUnavailable, match="invalid_ml_mode"):
        evaluate()


@pytest.mark.parametrize("cents,expected", [(1, .01), (249, 2.49), (250, 2.5),
                                            (749, 7.49), (750, 7.5), (1250, 12.5), (1234567, 12345.67)])
def test_native_tiyn_precision_has_no_foreign_currency_rounding_or_minimum(cents, expected):
    assert kzt_amount(cents) == expected
    result = evaluate([row("historical", cents=cents)], amount_cents=cents)
    assert result["status"] == "available", result
    assert result["features"]["amount"] == expected
    assert result["features"]["sender_avg_amount_30d"] == expected
