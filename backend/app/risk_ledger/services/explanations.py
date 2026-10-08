from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.risk_ledger.core.config import SHAP_FACTOR_COUNT
from app.risk_ledger.domain.models import ExplanationFactor, ModelManifest


def _display_value(value: Any) -> str | float | int | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value)
    text = str(value)
    return text if len(text) <= 120 else text[:117] + "..."


def explain_batch(
    model: Any,
    features: pd.DataFrame,
    manifest: ModelManifest,
    *,
    factor_count: int = SHAP_FACTOR_COUNT,
) -> list[list[ExplanationFactor]]:
    """Return the strongest per-row native CatBoost SHAP contributions."""
    if features.empty:
        return []
    if factor_count < 1:
        raise ValueError("factor_count must be positive.")

    from catboost import Pool
    pool = Pool(features, cat_features=manifest.categorical_features)
    shap_values = np.asarray(
        model.get_feature_importance(pool, type="ShapValues"),
        dtype="float64",
    )
    expected_shape = (len(features), len(manifest.feature_columns) + 1)
    if shap_values.shape != expected_shape:
        raise ValueError(
            f"Unexpected SHAP shape {shap_values.shape}; expected {expected_shape}."
        )

    limit = min(factor_count, 5, len(manifest.feature_columns))
    explanations: list[list[ExplanationFactor]] = []
    contributions = shap_values[:, :-1]
    for row_number, row_values in enumerate(contributions):
        strongest = np.argsort(-np.abs(row_values), kind="stable")[:limit]
        factors = []
        for feature_index in strongest:
            contribution = float(row_values[feature_index])
            canonical_feature = manifest.feature_columns[int(feature_index)]
            feature = next(
                (
                    alias
                    for alias in manifest.feature_sources.get(canonical_feature, [])
                    if alias != canonical_feature
                ),
                canonical_feature,
            )
            factors.append(
                ExplanationFactor(
                    feature=feature,
                    value=_display_value(features.iloc[row_number, int(feature_index)]),
                    contribution=contribution,
                    direction=(
                        "increases_risk" if contribution >= 0 else "decreases_risk"
                    ),
                )
            )
        explanations.append(factors)
    return explanations
