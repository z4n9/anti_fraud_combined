from __future__ import annotations

from dataclasses import asdict
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

from app.risk_ledger.core.config import IDENTIFIER_COLUMNS, PREDICTION_BATCH_SIZE
from app.risk_ledger.domain.models import ModelManifest, PredictionBatch, RiskLevel
from app.risk_ledger.services.explanations import explain_batch
from app.risk_ledger.services.preprocessing import normalize_headers, transform_features


OUTPUT_COLUMNS = frozenset(
    {
        "risk_probability",
        "risk_level",
        "requires_review",
        "explanation_factors",
        "analysis_warnings",
    }
)


def _json_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value


def _record_ids(frame: pd.DataFrame) -> list[str]:
    candidates = [
        name
        for name in frame.columns
        if name.lower() in IDENTIFIER_COLUMNS or name.lower().endswith("_id")
    ]
    for name in candidates:
        values = frame[name].astype("string").str.strip()
        if values.notna().all() and (values != "").all() and values.is_unique:
            return values.astype(str).tolist()
    width = max(6, len(str(max(len(frame), 1))))
    return [f"row-{position:0{width}d}" for position in range(1, len(frame) + 1)]


def risk_level(probability: float, boundaries: dict[str, float]) -> RiskLevel:
    medium = float(boundaries["medium"])
    high = float(boundaries["high"])
    critical = float(boundaries["critical"])
    if not 0 <= medium <= high <= critical <= 1:
        raise ValueError("Risk boundaries must be ordered values between 0 and 1.")
    if probability >= critical:
        return "critical"
    if probability >= high:
        return "high"
    if probability >= medium:
        return "medium"
    return "low"


def predict_frame(
    frame: pd.DataFrame,
    model: Any,
    manifest: ModelManifest,
    *,
    threshold: float | None = None,
    warnings: list[str] | None = None,
    batch_size: int = PREDICTION_BATCH_SIZE,
    cancel_check: Callable[[], None] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> PredictionBatch:
    if batch_size < 1:
        raise ValueError("batch_size must be positive.")
    active_threshold = manifest.review_threshold if threshold is None else float(threshold)
    if not 0 <= active_threshold <= 1:
        raise ValueError("threshold must be between 0 and 1.")

    normalized = normalize_headers(frame).reset_index(drop=True)
    record_ids = _record_ids(normalized)
    common_warnings = list(warnings or [])
    rows: list[dict[str, Any]] = []
    probabilities: list[float] = []

    for start in range(0, len(normalized), batch_size):
        if cancel_check is not None:
            cancel_check()
        stop = min(start + batch_size, len(normalized))
        raw_batch = normalized.iloc[start:stop]
        features = transform_features(raw_batch, manifest)
        batch_probabilities = np.asarray(model.predict_proba(features), dtype="float64")[:, 1]
        if not np.isfinite(batch_probabilities).all():
            raise ValueError("Model returned non-finite probabilities.")
        explanations = explain_batch(model, features, manifest)

        for offset, probability_value in enumerate(batch_probabilities):
            absolute_position = start + offset
            probability = float(probability_value)
            if not 0 <= probability <= 1:
                raise ValueError("Model returned a probability outside [0, 1].")
            source = {
                str(column): _json_value(value)
                for column, value in raw_batch.iloc[offset].items()
                if str(column) not in OUTPUT_COLUMNS and str(column) != "record_id"
            }
            source.update(
                {
                    "record_id": record_ids[absolute_position],
                    "risk_probability": probability,
                    "risk_level": risk_level(probability, manifest.risk_boundaries),
                    "requires_review": probability >= active_threshold,
                    "explanation_factors": [
                        asdict(factor) for factor in explanations[offset]
                    ],
                    "analysis_warnings": common_warnings.copy(),
                }
            )
            rows.append(source)
            probabilities.append(probability)
        if progress_callback is not None:
            progress_callback(stop, len(normalized))

    if cancel_check is not None:
        cancel_check()

    return PredictionBatch(
        rows=rows,
        probabilities=probabilities,
        threshold=active_threshold,
    )
