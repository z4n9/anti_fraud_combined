"""Server balance is private context and a guard, never a new Booster feature."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math

import pytest

from app.ml import MLUnavailable, evaluate_synsepa
from app.ml import service
from app.ml.balance_context import balance_rule, build_balance_context
from app.ml.manifest import ASSET_SHA256

NOW = datetime(2026, 11, 4, 7, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def enable_balance_tests(monkeypatch):
    monkeypatch.setenv("AMAN_ML_MODE", "auto")
    monkeypatch.delenv("AMAN_ML_ASSET_DIR", raising=False)


def evaluate(*, amount=8000, balance=None, history=()):
    return evaluate_synsepa(list(history), sender_id="sender", receiver_id="new", amount_cents=amount,
                            evaluated_at=NOW, sender_balance_cents=balance)


@pytest.mark.parametrize("amount,balance,triggered,projected", [
    (7999, 10000, False, 2001), (8000, 10000, True, 2000), (8001, 10000, True, 1999),
    (1, 1, True, 0), (1, 2, False, 1), (1, 0, False, -1), (101, 100, True, -1),
    (8 * 10 ** 19 - 1, 10 ** 20, False, 2 * 10 ** 19 + 1),
    (8 * 10 ** 19, 10 ** 20, True, 2 * 10 ** 19),
])
def test_exact_integer_80_percent_boundary_and_projected_balance(amount, balance, triggered, projected):
    context = build_balance_context(amount_cents=amount, sender_balance_cents=balance)
    assert context["balance_before_cents"] == balance
    assert context["amount_cents"] == amount
    assert context["projected_balance_after_cents"] == projected
    assert context["context_version"] == "sender-balance-v1" and context["rule_threshold"] == .8
    assert context["amount_to_balance_ratio"] == (amount / balance if balance else None)
    assert balance_rule(context)["triggered"] is triggered
    json.dumps(context, allow_nan=False)


@pytest.mark.parametrize("invalid", [True, False, "10000", -1, math.nan, math.inf, 1.5])
@pytest.mark.parametrize("mode", ["auto", "required", "off"])
def test_invalid_supplied_balance_is_never_silently_dropped(monkeypatch, invalid, mode):
    monkeypatch.setenv("AMAN_ML_MODE", mode)
    with pytest.raises(MLUnavailable, match="invalid_balance_context"):
        evaluate(balance=invalid)


@pytest.mark.parametrize("invalid", [True, "100", 0, -1, math.nan, math.inf, 1.5])
def test_invalid_amount_with_supplied_context_is_explicit(monkeypatch, invalid):
    monkeypatch.setenv("AMAN_ML_MODE", "off")
    with pytest.raises(MLUnavailable, match="invalid_balance_context"):
        evaluate(amount=invalid, balance=10000)


def test_balance_changes_guard_but_not_score_ml_decision_or_any_trained_feature(monkeypatch):
    model, _, _, _ = service._bundle(service.get_ml_status()["checksums"])
    monkeypatch.setattr(model, "predict", lambda features: 0.4)
    no_context = evaluate()
    below = evaluate(balance=10001)
    boundary = evaluate(balance=10000)
    assert no_context["status"] == below["status"] == boundary["status"] == "available"
    assert len(boundary["features"]) == 21
    assert no_context["features"] == below["features"] == boundary["features"]
    assert no_context["score"] == below["score"] == boundary["score"]
    assert no_context["ml_decision"] == below["ml_decision"] == boundary["ml_decision"]
    assert no_context["rule_decision"] == below["rule_decision"] == boundary["rule_decision"] == "ALLOW"
    assert boundary["decision"] == "REVIEW"
    assert "balance_context" not in no_context and "balance_rule" not in no_context
    assert below["balance_rule"]["evaluated"] and not below["balance_rule"]["triggered"]
    assert boundary["balance_rule"]["evaluated"] and boundary["balance_rule"]["triggered"]
    assert no_context["triggered_rules"] == boundary["triggered_rules"]
    assert set(boundary["balance_context"]) == {"balance_before_cents", "amount_cents",
        "projected_balance_after_cents", "amount_to_balance_ratio", "context_version", "rule_threshold"}
    assert all("balance" not in key for key in boundary["features"])
    # No per-request context is retained in the cached bundle.
    assert evaluate() == no_context


def test_tx_receives_kzt_balance_without_adding_a_model_feature(monkeypatch):
    from app.ml.online_features import OnlineFeatureBuilder
    observed = []
    build = OnlineFeatureBuilder.build

    def observe(self, tx, *args, **kwargs):
        observed.append(tx.sender_balance)
        return build(self, tx, *args, **kwargs)

    monkeypatch.setattr(OnlineFeatureBuilder, "build", observe)
    result = evaluate(amount=1, balance=1234567)
    assert result["status"] == "available"
    assert observed == [12345.67]
    assert result["balance_context"]["projected_balance_after_cents"] == 1234566
    assert len(result["features"]) == 21


@pytest.mark.parametrize("score,decision", [(0.01, "ALLOW"), (0.9, "HIGH_RISK")])
def test_balance_guard_skips_decisive_model_results(monkeypatch, score, decision):
    model, _, _, _ = service._bundle(service.get_ml_status()["checksums"])
    monkeypatch.setattr(model, "predict", lambda features: score)
    result = evaluate(balance=10000)
    assert result["ml_decision"] == result["decision"] == decision
    assert result["balance_rule"]["evaluated"] is False
    assert result["balance_rule"]["triggered"] is False


def test_real_source_review_plus_balance_remains_review_and_cache_config_is_unchanged():
    status = service.get_ml_status()
    _, risk, _, _ = service._bundle(status["checksums"])
    before = deepcopy(risk.cfg)
    baseline = evaluate(amount=110000000)
    contextual = evaluate(amount=110000000, balance=120000000)
    assert baseline["rule_decision"] == contextual["rule_decision"] == "REVIEW"
    assert contextual["rule_families"] == baseline["rule_families"] == ["cold_start"]
    assert contextual["ml_decision"] == "ALLOW" and not contextual["balance_rule"]["evaluated"]
    assert baseline["score"] == contextual["score"]
    assert risk.cfg == before


@pytest.mark.parametrize("failure", ["disabled", "missing", "inference"])
def test_balance_context_survives_disabled_and_auto_unavailable_without_fake_ml(failure, monkeypatch, tmp_path):
    if failure == "disabled":
        monkeypatch.setenv("AMAN_ML_MODE", "off")
    elif failure == "missing":
        monkeypatch.setenv("AMAN_ML_ASSET_DIR", str(tmp_path / "no-assets"))
    else:
        status = service.get_ml_status()
        model, _, _, _ = service._bundle(status["checksums"])
        monkeypatch.setattr(model, "predict", lambda features: (_ for _ in ()).throw(RuntimeError("inference failed")))
    result = evaluate(balance=10000)
    assert result["status"] == ("disabled" if failure == "disabled" else "unavailable")
    assert result["balance_context"]["amount_to_balance_ratio"] == .8
    assert result["balance_rule"]["evaluated"] is True
    assert result["balance_rule"]["triggered"] is True
    assert not {"score", "ml_decision", "decision", "features"}.intersection(result)


def test_required_error_retains_valid_balance_context_for_private_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setenv("AMAN_ML_MODE", "required")
    monkeypatch.setenv("AMAN_ML_ASSET_DIR", str(tmp_path / "no-assets"))
    with pytest.raises(MLUnavailable) as exc:
        evaluate(balance=10000)
    assert exc.value.diagnostics["balance_context"]["balance_before_cents"] == 10000
    assert exc.value.diagnostics["balance_rule"]["evaluated"] is False
    assert exc.value.diagnostics["balance_rule"]["triggered"] is False


def test_balance_access_never_changes_assets_or_manifest():
    before = {name: hashlib.sha256((service.ASSET_DIR / name).read_bytes()).hexdigest() for name in ASSET_SHA256}
    assert before == ASSET_SHA256
    evaluate(balance=10000)
    after = {name: hashlib.sha256((service.ASSET_DIR / name).read_bytes()).hexdigest() for name in ASSET_SHA256}
    assert after == before
