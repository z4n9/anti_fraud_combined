from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)

from app.risk_ledger.domain.models import AnalysisMetrics
from app.risk_ledger.services.preprocessing import ensure_binary_target


def unavailable_metrics(threshold: float, reason: str) -> AnalysisMetrics:
    return AnalysisMetrics(
        available=False,
        threshold=float(threshold),
        unavailable_reason=reason,
    )


def calculate_metrics(
    target: pd.Series | None,
    probabilities: list[float] | np.ndarray,
    threshold: float,
) -> AnalysisMetrics:
    if target is None:
        return unavailable_metrics(threshold, "Target column is not present.")

    try:
        y_true = ensure_binary_target(target)
    except ValueError as error:
        return unavailable_metrics(threshold, str(error))

    scores = np.asarray(probabilities, dtype="float64")
    if len(scores) != len(y_true):
        raise ValueError("Target and probability arrays must have equal length.")
    if not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any():
        raise ValueError("Probabilities must be finite values between 0 and 1.")

    predictions = (scores >= threshold).astype("int8")
    roc_auc = float(roc_auc_score(y_true, scores))
    false_positive_rate, true_positive_rate, _ = roc_curve(y_true, scores)
    matrix = confusion_matrix(y_true, predictions, labels=[0, 1]).astype(int)
    return AnalysisMetrics(
        available=True,
        threshold=float(threshold),
        gini=float(2 * roc_auc - 1),
        ks=float(np.max(true_positive_rate - false_positive_rate)),
        accuracy=float(accuracy_score(y_true, predictions)),
        precision=float(precision_score(y_true, predictions, zero_division=0)),
        recall=float(recall_score(y_true, predictions, zero_division=0)),
        roc_auc=roc_auc,
        pr_auc=float(average_precision_score(y_true, scores)),
        confusion_matrix=matrix.tolist(),
    )
