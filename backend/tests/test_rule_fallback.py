"""Checks fallback correctness, independent of synthetic control labels and ML."""
import builtins
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.transaction_rules import (
    BoundedTransactionGraph, ScenarioRuleConfig, TransactionScenarioEngine,
    analyze_transaction_risk, default_rule_reference,
)
from app.services.transaction_features import TransactionFeatureBuilder


def _row(identifier="T1", amount=1_000_000, timestamp="2026-01-01T10:00:00Z", **extras):
    return {
        "transaction_id": identifier, "client_id": "C1", "sender_account_id": "A1",
        "recipient_account_id": "R1", "transaction_amount": amount,
        "transaction_timestamp": timestamp, **extras,
    }


@pytest.mark.parametrize("corrupt", [False, True])
def test_absent_or_corrupt_model_uses_rules_without_quality_discount(tmp_path, corrupt):
    source = tmp_path / "transactions.csv"
    pd.DataFrame([_row()]).to_csv(source, index=False)
    artifacts = tmp_path / "models"
    if corrupt:
        artifacts.mkdir()
        (artifacts / "manifest.json").write_text("{broken", encoding="utf-8")
    scores, manifest = analyze_transaction_risk(source, artifacts, data_quality_score=0)
    first = scores.iloc[0]
    assert first.risk_signal_score == pytest.approx(0.9)
    assert first.requires_review
    assert first.ml_anomaly_score is None
    assert first.data_uncertainty == 1
    assert manifest["model_version"] is None
    assert manifest["warning"]
    assert manifest["engine_mode"] == "rule_based_fallback"
    assert manifest["reference_origin"] == "fixed_demo_baseline"
    complete, _ = analyze_transaction_risk(source, artifacts, data_quality_score=1)
    pd.testing.assert_series_equal(scores["risk_signal_score"], complete["risk_signal_score"])


def test_fallback_works_when_optional_ml_dependency_is_unavailable(tmp_path, monkeypatch):
    source = tmp_path / "transactions.csv"
    pd.DataFrame([_row()]).to_csv(source, index=False)
    original_import = builtins.__import__

    def restricted_import(name, *args, **kwargs):
        if name == "joblib" or name.startswith("sklearn"):
            raise ImportError("optional ML unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", restricted_import)
    scores, manifest = analyze_transaction_risk(source, tmp_path / "models")
    assert manifest["engine_mode"] == "rule_based_fallback"
    assert scores.iloc[0].risk_signal_score > 0


@pytest.mark.parametrize("damage", ["reference", "normalization", "profile_scale", "score_failure"])
def test_malformed_model_configuration_and_model_errors_fallback(tmp_path, damage):
    import joblib

    source = tmp_path / "transactions.csv"
    pd.DataFrame([_row()]).to_csv(source, index=False)
    artifacts = tmp_path / "models"
    artifacts.mkdir()
    reference = default_rule_reference()
    # All valid manifest fields, but a model without score_samples simulates runtime failure.
    model = SimpleNamespace(feature_names_in_=reference.feature_columns)
    joblib.dump(model, artifacts / "model.joblib")
    manifest = {
        "model_version": "test", "feature_reference": reference.to_dict(),
        "feature_columns": reference.feature_columns, "review_threshold": 0.72,
        "score_normalization": {"lower": 0, "upper": 1},
        "feature_profiles": {key: {"median": 0, "scale": 1} for key in reference.feature_columns},
        "feature_labels": reference.feature_labels,
    }
    if damage == "reference":
        manifest.pop("feature_reference")
    elif damage == "normalization":
        manifest["score_normalization"] = {"lower": 1, "upper": 0}
    elif damage == "profile_scale":
        manifest["feature_profiles"][reference.feature_columns[0]]["scale"] = 0
    (artifacts / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    scores, diagnostic = analyze_transaction_risk(source, artifacts)
    assert diagnostic["engine_mode"] == "rule_based_fallback"
    assert scores.iloc[0].risk_signal_score == pytest.approx(0.9)


def test_fallback_batch_invariance_and_labels_do_not_affect_scores(tmp_path):
    source = tmp_path / "transactions.csv"
    rows = pd.DataFrame([
        _row("T1", 100_000, "2026-01-01T10:00:00Z", scenario="normal", is_anomaly=0),
        _row("T2", 1_000_000, "2026-01-01T10:01:00Z", scenario="large_amount", is_anomaly=1),
    ])
    rows.to_csv(source, index=False)
    small, _ = analyze_transaction_risk(source, tmp_path / "models", chunk_size=1)
    large, _ = analyze_transaction_risk(source, tmp_path / "models", chunk_size=100)
    pd.testing.assert_frame_equal(small, large)
    rows["scenario"] = ["large_amount", "normal"]
    rows["is_anomaly"] = [1, 0]
    rows.to_csv(source, index=False)
    changed, _ = analyze_transaction_risk(source, tmp_path / "models")
    columns = ["transaction_id", "risk_signal_score", "rule_explanation", "graph_explanation"]
    pd.testing.assert_frame_equal(small[columns], changed[columns])


def test_later_file_rows_do_not_change_first_fallback_decision(tmp_path):
    source = tmp_path / "transactions.csv"
    rows = [_row()]
    pd.DataFrame(rows).to_csv(source, index=False)
    first, _ = analyze_transaction_risk(source, tmp_path / "models")
    rows.append(_row("T2", 100_000_000, "2026-01-01T11:00:00Z"))
    pd.DataFrame(rows).to_csv(source, index=False)
    extended, _ = analyze_transaction_risk(source, tmp_path / "models")
    assert extended.loc[extended.transaction_id.eq("T1"), "risk_signal_score"].iloc[0] == first.iloc[0].risk_signal_score


def test_online_config_excludes_inbound_from_velocity_but_detects_cashout():
    engine = TransactionScenarioEngine(default_rule_reference(), ScenarioRuleConfig(outbound_history_only=True))
    inbound = [_row(f"I{i}", 10_000, f"2026-01-01T10:0{i}:00Z", direction="inbound") for i in range(6)]
    engine.evaluate_chunk(pd.DataFrame(inbound))
    result = engine.evaluate_chunk(pd.DataFrame([_row("O1", 60_000, "2026-01-01T10:06:00Z", direction="outbound")]))
    codes = {item["code"] for item in json.loads(result.iloc[0].rule_explanation)}
    assert "rapid_velocity" not in codes
    assert "rapid_cashout" in codes
    assert len(engine._history["C1"]) == 1


def test_night_rule_uses_configured_local_hour():
    row = pd.DataFrame([_row(timestamp="2026-01-01T20:00:00Z", amount=100_000)])
    utc = TransactionScenarioEngine(default_rule_reference()).evaluate_chunk(row)
    local = TransactionScenarioEngine(default_rule_reference(), ScenarioRuleConfig(night_utc_offset_hours=5)).evaluate_chunk(row)
    assert "night_activity" not in {x["code"] for x in json.loads(utc.iloc[0].rule_explanation)}
    night = next(x for x in json.loads(local.iloc[0].rule_explanation) if x["code"] == "night_activity")
    assert night["evidence"]["hour_local"] == 1


def test_equal_time_features_ignore_peer_rows_across_chunk_boundaries():
    rows = pd.DataFrame([
        _row("P", 10_000, "2026-01-01T09:59:00Z", recipient_account_id="OLD", direction="inbound"),
        _row("E1", 1_000_000, "2026-01-01T10:00:00Z", direction="inbound"),
        _row("E2", 1_000_000, "2026-01-01T10:00:00Z", direction="outbound"),
        _row("L", 1_000_000, "2026-01-01T10:01:00Z", direction="outbound"),
    ])
    all_rows = TransactionFeatureBuilder(default_rule_reference()).transform_chunk(rows)
    engine = TransactionFeatureBuilder(default_rule_reference())
    one_at_a_time = pd.concat([engine.transform_chunk(rows.iloc[i:i+1]) for i in range(len(rows))])
    pd.testing.assert_frame_equal(all_rows, one_at_a_time)
    assert all_rows.prior_count_5m.tolist() == [0, 1, 1, 3]
    assert all_rows.is_new_recipient.tolist() == [1, 1, 1, 0]
    assert all_rows.minutes_since_previous.tolist() == [43_200, 1, 1, 1]
    assert all_rows.iloc[2].minutes_since_inbound == 1
    assert all_rows.iloc[1].client_amount_robust_z == all_rows.iloc[2].client_amount_robust_z


def test_equal_time_rules_ignore_peer_baseline_device_pair_and_inbound():
    prior = [
        _row(f"P{i}", 50_000, f"2026-01-01T23:0{i}:00Z", recipient_account_id="OLD",
             device_id="OLD", country="KZ", currency="KZT") for i in range(5)
    ]
    same = [
        _row(f"E{i}", 1_000_000, "2026-01-02T00:00:00Z", device_id="NEW", country="ZZ", currency="USD")
        for i in range(6)
    ]
    rows = pd.DataFrame(prior + same)
    large = TransactionScenarioEngine(default_rule_reference()).evaluate_chunk(rows)
    engine = TransactionScenarioEngine(default_rule_reference())
    small = pd.concat([engine.evaluate_chunk(rows.iloc[i:i+1]) for i in range(len(rows))], ignore_index=True)
    pd.testing.assert_frame_equal(large, small)
    assert len(set(large.iloc[5:].rule_explanation)) == 1
    assert all("behavior_deviation" in x for x in large.iloc[5:].rule_explanation)
    assert all("new_recipient" in x for x in large.iloc[5:].rule_explanation)
    assert all("rapid_velocity" not in x for x in large.iloc[5:].rule_explanation)
    inbound_outbound = pd.DataFrame([
        _row("I", 100_000, "2026-01-02T10:00:00Z", direction="inbound"),
        _row("O", 100_000, "2026-01-02T10:00:00Z", direction="outbound"),
        _row("O2", 100_000, "2026-01-02T10:01:00Z", direction="outbound"),
    ])
    cashout = TransactionScenarioEngine(default_rule_reference()).evaluate_chunk(inbound_outbound)
    assert "rapid_cashout" not in cashout.iloc[1].rule_explanation
    assert "rapid_cashout" in cashout.iloc[2].rule_explanation


def test_equal_time_graph_ignores_peer_edges_in_signals_and_features():
    config = ScenarioRuleConfig(many_to_one_senders_1h=2)
    graph = BoundedTransactionGraph(config)
    timestamp = pd.Timestamp("2026-01-01T10:00:00Z")
    graph.evaluate_and_add(timestamp=timestamp, sender="A", recipient="R", amount=100)
    signals, features = graph.evaluate_and_add(timestamp=timestamp, sender="B", recipient="R", amount=200)
    assert not signals
    assert features["recipient_in_degree_24h"] == 1
    assert features["recipient_incoming_amount_24h"] == 200
    signals, _ = graph.evaluate_and_add(timestamp=timestamp, sender="R", recipient="A", amount=100)
    assert not signals
    signals, features = graph.evaluate_and_add(timestamp=timestamp + pd.Timedelta(minutes=1), sender="C", recipient="R", amount=100)
    assert any(x["code"] == "many_to_one" for x in signals)
    assert features["recipient_in_degree_24h"] == 3
    assert features["recipient_incoming_amount_24h"] == 400


def test_equal_time_rule_graph_snapshot_is_chunk_invariant():
    rows = pd.DataFrame([
        _row("E1", 100_000, sender_account_id="A", recipient_account_id="R"),
        _row("E2", 100_000, sender_account_id="B", recipient_account_id="R"),
        _row("E3", 100_000, sender_account_id="R", recipient_account_id="A"),
        _row("L", 100_000, "2026-01-01T10:01:00Z", sender_account_id="C", recipient_account_id="R"),
    ])
    config = ScenarioRuleConfig(many_to_one_senders_1h=2, max_graph_edges=100)
    large = TransactionScenarioEngine(default_rule_reference(), config).evaluate_chunk(rows)
    engine = TransactionScenarioEngine(default_rule_reference(), config)
    small = pd.concat([engine.evaluate_chunk(rows.iloc[i:i+1]) for i in range(len(rows))], ignore_index=True)
    pd.testing.assert_frame_equal(large, small)
    assert large.iloc[:3].graph_score.eq(0).all()
    assert large.iloc[-1].graph_score == 1
