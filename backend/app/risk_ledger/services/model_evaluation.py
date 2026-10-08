from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score


def expected_calibration_error(
    labels: Sequence[int], probabilities: Sequence[float], *, bins: int = 10
) -> float:
    y = np.asarray(labels, dtype=int)
    p = np.asarray(probabilities, dtype=float)
    if len(y) != len(p) or not len(y):
        raise ValueError("Labels and probabilities must be non-empty and aligned.")
    total = len(y)
    error = 0.0
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        mask = (p >= lower) & (p < upper if index < bins - 1 else p <= upper)
        if not mask.any():
            continue
        error += float(mask.mean()) * abs(float(y[mask].mean()) - float(p[mask].mean()))
    return error


def population_stability_index(
    reference: Sequence[float], candidate: Sequence[float], *, bins: int = 10
) -> float:
    baseline = np.asarray(reference, dtype=float)
    current = np.asarray(candidate, dtype=float)
    if not len(baseline) or not len(current):
        raise ValueError("PSI samples must not be empty.")
    edges = np.unique(np.quantile(baseline, np.linspace(0, 1, bins + 1)))
    if len(edges) < 2:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    expected = np.histogram(baseline, bins=edges)[0] / len(baseline)
    actual = np.histogram(current, bins=edges)[0] / len(current)
    expected = np.clip(expected, 1e-6, None)
    actual = np.clip(actual, 1e-6, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def build_candidate_evaluation(
    *,
    labels: Sequence[int],
    probabilities: Sequence[float],
    timestamps: Sequence[Any],
    feature_columns: Sequence[str],
    target_column: str,
    reference_scores: Sequence[float],
    champion_alert_rate: float,
    threshold: float,
    windows: int = 3,
) -> dict[str, Any]:
    y = np.asarray(labels, dtype=int)
    p = np.asarray(probabilities, dtype=float)
    moments = pd.to_datetime(pd.Series(timestamps), errors="coerce", utc=True)
    if len(y) != len(p) or len(y) != len(moments) or len(y) < 100:
        raise ValueError("Evaluation requires at least 100 aligned labeled rows with timestamps.")
    if moments.isna().any() or len(np.unique(y)) < 2:
        raise ValueError("Evaluation requires valid timestamps and both target classes.")
    order = np.argsort(moments.to_numpy())
    overall_roc = float(roc_auc_score(y, p))
    overall_pr = float(average_precision_score(y, p))
    window_metrics: list[dict[str, Any]] = []
    for window, indices in enumerate(np.array_split(order, windows), start=1):
        if len(indices) == 0 or len(np.unique(y[indices])) < 2:
            continue
        window_metrics.append({
            "window": window,
            "rows": int(len(indices)),
            "roc_auc": float(roc_auc_score(y[indices], p[indices])),
            "pr_auc": float(average_precision_score(y[indices], p[indices])),
        })
    max_drop = max((overall_roc - item["roc_auc"] for item in window_metrics), default=1.0)
    normalized_features = {str(name).casefold() for name in feature_columns}
    suspected = sorted(
        name for name in normalized_features
        if name == target_column.casefold() or name in {"label", "fraud_label", "is_fraud", "confirmed_fraud"}
    )
    alert_rate = float(np.mean(p >= threshold))
    return {
        "holdout": {"rows": int(len(y)), "roc_auc": overall_roc, "pr_auc": overall_pr},
        "temporal_backtest": {"windows": len(window_metrics), "max_metric_drop": float(max_drop), "metrics": window_metrics},
        "drift": {"max_psi": population_stability_index(reference_scores, p)},
        "leakage": {"target_as_feature": target_column.casefold() in normalized_features, "suspected_features": suspected},
        "calibration": {"rows": int(len(y)), "expected_calibration_error": expected_calibration_error(y, p)},
        "alert_volume": {"rate": alert_rate, "champion_delta": alert_rate - float(champion_alert_rate)},
    }
