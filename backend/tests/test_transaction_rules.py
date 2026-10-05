from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from app.services.transaction_features import TransactionFeatureReference, fit_transaction_reference
from app.services.transaction_rules import (
    BoundedTransactionGraph,
    ScenarioRuleConfig,
    TransactionScenarioEngine,
    analyze_transaction_risk,
    score_transaction_rules,
)
from app.training.generate_synthetic_transactions import generate_synthetic_dataset
from app.training.train_transactions import train_transaction_model


EXPECTED_SIGNAL = {
    "large_amount": "large_amount",
    "frequency_spike": "daily_velocity",
    "rapid_series": "rapid_velocity",
    "structuring": "structuring",
    "new_recipient": "new_recipient",
    "night_activity": "night_activity",
    "many_to_one": "many_to_one",
    "cycle": "short_cycle",
    "rapid_cashout": "rapid_cashout",
    "dormant_reactivation": "dormant_account",
    "behavior_deviation": "behavior_deviation",
}


@pytest.fixture(scope="module")
def scenario_scores(tmp_path_factory: pytest.TempPathFactory) -> pd.DataFrame:
    fixture = tmp_path_factory.mktemp("transaction-rules")
    generate_synthetic_dataset(fixture, seed=20260928, normal_rows=1_000)
    source = fixture / "transactions.csv"
    reference = fit_transaction_reference(source, chunk_size=137)
    scores, _ = score_transaction_rules(source, reference, chunk_size=73)
    return scores


@pytest.mark.parametrize("scenario,expected", EXPECTED_SIGNAL.items())
def test_each_control_scenario_is_detected(
    scenario_scores: pd.DataFrame,
    scenario: str,
    expected: str,
) -> None:
    subset = scenario_scores.loc[scenario_scores["scenario"] == scenario]
    codes = {
        item["code"]
        for column in ("rule_explanation", "graph_explanation")
        for payload in subset[column]
        for item in json.loads(payload)
    }
    assert expected in codes


def test_rules_do_not_overwhelm_normal_operations(scenario_scores: pd.DataFrame) -> None:
    normal = scenario_scores.loc[scenario_scores["scenario"] == "normal"]
    signaled = normal["scenario_score"].gt(0) | normal["graph_score"].gt(0)
    assert float(signaled.mean()) <= 0.03


def test_rule_scoring_is_invariant_across_chunks(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    generate_synthetic_dataset(fixture, seed=107, normal_rows=1_000)
    source = fixture / "transactions.csv"
    reference = fit_transaction_reference(source)
    small, _ = score_transaction_rules(source, reference, chunk_size=31)
    large, _ = score_transaction_rules(source, reference, chunk_size=5_000)
    columns = ["transaction_id", "scenario_score", "graph_score", "rule_explanation", "graph_explanation"]
    pd.testing.assert_frame_equal(small[columns], large[columns])


def test_graph_is_bounded() -> None:
    config = ScenarioRuleConfig(max_graph_edges=5)
    graph = BoundedTransactionGraph(config)
    for index in range(20):
        graph.evaluate_and_add(
            timestamp=pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=index),
            sender=f"A{index}",
            recipient="B",
            amount=100.0,
        )
    assert graph.edge_count <= 5
    assert graph.max_observed_edges <= 5


def test_synthetic_control_columns_cannot_change_detection() -> None:
    reference = TransactionFeatureReference(
        schema_version="1.0",
        amount_median=100.0,
        amount_mad=20.0,
        categories={"currency": [], "channel": [], "country": [], "direction": []},
    )
    rows = pd.DataFrame([
        {
            "transaction_id": "T1", "client_id": "C1", "sender_account_id": "A1",
            "recipient_account_id": "R1", "transaction_timestamp": "2026-01-01T10:00:00Z",
            "transaction_amount": 100.0, "scenario": "large_amount", "is_anomaly": 1,
            "synthetic_only": 1,
        },
        {
            "transaction_id": "T2", "client_id": "C1", "sender_account_id": "A1",
            "recipient_account_id": "R1", "transaction_timestamp": "2026-01-01T10:01:00Z",
            "transaction_amount": 100.0, "scenario": "normal", "is_anomaly": 0,
            "synthetic_only": 1,
        },
    ])
    changed = rows.copy()
    changed["scenario"] = ["normal", "behavior_deviation"]
    changed["is_anomaly"] = [0, 1]
    first = TransactionScenarioEngine(reference).evaluate_chunk(rows)
    second = TransactionScenarioEngine(reference).evaluate_chunk(changed)
    compared = ["scenario_score", "graph_score", "rule_explanation", "graph_explanation"]
    pd.testing.assert_frame_equal(first[compared], second[compared])


def test_combined_result_keeps_ml_rules_and_graph_explanations_separate(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    artifacts = tmp_path / "artifacts"
    generate_synthetic_dataset(fixture, seed=109, normal_rows=1_000)
    source = fixture / "transactions.csv"
    train_transaction_model(source, artifacts, estimators=20, chunk_size=127)
    scores, manifest = analyze_transaction_risk(source, artifacts, chunk_size=79)

    assert {"ml_explanation", "rule_explanation", "graph_explanation"}.issubset(scores.columns)
    assert manifest["risk_formula"]["version"] == "1.1"
    assert manifest["result_term"] == "risk_signal_not_fraud_proof"
    first = scores.iloc[0]
    expected_score = max(
        0.60 * first["ml_anomaly_score"] + 0.25 * first["scenario_score"] + 0.15 * first["graph_score"],
        0.85 * first["ml_anomaly_score"],
        0.90 * first["scenario_score"],
        0.90 * first["graph_score"],
    )
    assert first["risk_signal_score"] == pytest.approx(expected_score)
    uncertain, _ = analyze_transaction_risk(source, artifacts, chunk_size=79, data_quality_score=0.1)
    pd.testing.assert_series_equal(scores["risk_signal_score"], uncertain["risk_signal_score"])
    assert uncertain["data_uncertainty"].eq(0.9).all()
    assert all(
        item["explanation_type"] == "deviation_from_training_norm"
        for item in json.loads(scores.iloc[0]["ml_explanation"])
    )
    assert all(
        item["explanation_type"] == "deterministic_rule"
        for payload in scores["rule_explanation"]
        for item in json.loads(payload)
    )
    assert all(
        item["explanation_type"] == "graph_signal"
        for payload in scores["graph_explanation"]
        for item in json.loads(payload)
    )
