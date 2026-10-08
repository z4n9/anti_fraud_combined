from __future__ import annotations

import pandas as pd
import pytest

from app.risk_ledger.services.metrics import calculate_metrics


def test_calculate_all_agreed_metrics() -> None:
    result = calculate_metrics(
        pd.Series([0, 0, 1, 1]),
        [0.1, 0.4, 0.35, 0.8],
        threshold=0.5,
    )

    assert result.available is True
    assert result.gini == pytest.approx(0.5)
    assert result.ks == pytest.approx(0.5)
    assert result.accuracy == pytest.approx(0.75)
    assert result.precision == pytest.approx(1.0)
    assert result.recall == pytest.approx(0.5)
    assert result.roc_auc == pytest.approx(0.75)
    assert result.pr_auc == pytest.approx(5 / 6)
    assert result.confusion_matrix == [[2, 0], [1, 1]]


def test_metrics_are_unavailable_without_valid_target() -> None:
    missing = calculate_metrics(None, [0.1, 0.9], threshold=0.5)
    invalid = calculate_metrics(pd.Series([0, 2]), [0.1, 0.9], threshold=0.5)

    assert missing.available is False
    assert "not present" in (missing.unavailable_reason or "")
    assert invalid.available is False
    assert "binary" in (invalid.unavailable_reason or "")
