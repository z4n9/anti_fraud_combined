from __future__ import annotations

import numpy as np
import pandas as pd

from app.risk_ledger.services.model_evaluation import build_candidate_evaluation, expected_calibration_error
from app.risk_ledger.services.model_registry import evaluate_candidate_gates


def test_builds_holdout_temporal_drift_leakage_calibration_and_volume_report() -> None:
    rng = np.random.default_rng(42)
    labels = np.array(([0] * 19 + [1]) * 30)
    probabilities = np.clip(labels * 0.90 + rng.normal(0.05, 0.01, len(labels)), 0.01, 0.99)
    report = build_candidate_evaluation(
        labels=labels,
        probabilities=probabilities,
        timestamps=pd.date_range("2025-01-01", periods=len(labels), freq="h"),
        feature_columns=["amount", "frequency"],
        target_column="fraud_target",
        reference_scores=probabilities + rng.normal(0, 0.005, len(labels)),
        champion_alert_rate=float(np.mean(probabilities >= 0.7)),
        threshold=0.7,
    )
    assert set(report) == {"holdout", "temporal_backtest", "drift", "leakage", "calibration", "alert_volume"}
    assert all(gate.passed for gate in evaluate_candidate_gates(report))


def test_calibration_error_penalizes_overconfident_predictions() -> None:
    good = expected_calibration_error([0, 0, 1, 1], [0.05, 0.1, 0.9, 0.95])
    poor = expected_calibration_error([0, 0, 1, 1], [0.8, 0.9, 0.1, 0.2])
    assert good < poor
