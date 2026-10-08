from __future__ import annotations

import pytest
pytest.importorskip('catboost', reason='Optional CatBoost dependency is absent; no client model is fabricated.')

import numpy as np
import pandas as pd

from app.risk_ledger.services.predictor import predict_frame
from app.risk_ledger.services.preprocessing import build_manifest


class FakeCatBoostModel:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        self.batch_sizes.append(len(features))
        probabilities = np.clip(features["signal"].to_numpy(dtype=float) / 10, 0, 1)
        return np.column_stack([1 - probabilities, probabilities])

    def get_feature_importance(self, pool, type: str):  # noqa: A002
        assert type == "ShapValues"
        rows, columns = pool.num_row(), pool.num_col()
        values = np.tile(np.arange(1, columns + 1, dtype=float), (rows, 1))
        return np.column_stack([values, np.zeros(rows)])


def _frame(include_target: bool) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "signal": [1, 9, 6],
            "amount": [100, 900, 600],
            "term": [12, 24, 18],
            "income": [500, 400, 300],
            "age": [20, 50, 35],
            "GENDER": ["515", "516", "515"],
        }
    )
    if include_target:
        frame["GB_flag"] = [0, 1, 1]
    return frame


def test_batch_prediction_is_target_independent_and_explainable() -> None:
    manifest = build_manifest(_frame(include_target=True))
    manifest.review_threshold = 0.7
    model_with_target = FakeCatBoostModel()
    model_without_target = FakeCatBoostModel()

    with_target = predict_frame(
        _frame(include_target=True), model_with_target, manifest, batch_size=2
    )
    without_target = predict_frame(
        _frame(include_target=False), model_without_target, manifest, batch_size=2
    )

    assert with_target.probabilities == without_target.probabilities
    assert model_with_target.batch_sizes == [2, 1]
    assert [row["record_id"] for row in with_target.rows] == [
        "row-000001",
        "row-000002",
        "row-000003",
    ]
    assert [row["record_id"] for row in without_target.rows] == [
        "row-000001",
        "row-000002",
        "row-000003",
    ]
    assert [row["requires_review"] for row in with_target.rows] == [False, True, False]
    assert all(len(row["explanation_factors"]) == 5 for row in with_target.rows)
    assert all(
        factor["feature"] != "GB_flag"
        for row in with_target.rows
        for factor in row["explanation_factors"]
    )


def test_temporary_threshold_does_not_change_manifest() -> None:
    manifest = build_manifest(_frame(include_target=False))
    manifest.review_threshold = 0.8

    result = predict_frame(
        _frame(include_target=False),
        FakeCatBoostModel(),
        manifest,
        threshold=0.5,
    )

    assert result.threshold == 0.5
    assert manifest.review_threshold == 0.8
    assert [row["requires_review"] for row in result.rows] == [False, True, True]
