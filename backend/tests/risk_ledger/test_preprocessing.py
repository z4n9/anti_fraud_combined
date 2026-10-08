from __future__ import annotations

import pandas as pd

from app.risk_ledger.services.preprocessing import (
    build_manifest,
    find_conflicting_duplicate_features,
    transform_features,
)
from app.risk_ledger.services.schema_validator import validate_schema


def training_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            " amount ": ["100 000", "200 000", "300 000", "400 000"],
            "term": ["180", "365", "540", "720"],
            "NUM_CONTRACTS": ["-", "2", "-3", "4"],
            "GENDER": ["515", "516", "515", "516"],
            "optional": ["1", "-", "3", "4"],
            "empty": ["-", "-", "-", "-"],
            "GB_flag": [0, 1, 0, 1],
        }
    )


def test_manifest_excludes_target_identifiers_and_empty_columns() -> None:
    frame = training_frame()
    frame["client_id"] = ["a", "b", "c", "d"]

    manifest = build_manifest(frame)

    assert "GB_flag" not in manifest.feature_columns
    assert "client_id" not in manifest.feature_columns
    assert "client_id" in manifest.identifier_columns
    assert "empty" in manifest.dropped_features
    assert "GENDER" in manifest.categorical_features
    assert "amount" in manifest.numerical_features


def test_transform_applies_feature_specific_missing_rules() -> None:
    frame = training_frame()
    manifest = build_manifest(frame)

    transformed = transform_features(frame, manifest)

    assert transformed["NUM_CONTRACTS"].tolist() == [0.0, 2.0, 0.0, 4.0]
    assert transformed["GENDER"].tolist() == ["515", "516", "515", "516"]
    assert pd.isna(transformed.loc[1, "optional"])
    assert "GB_flag" not in transformed.columns


def test_schema_allows_reordered_extra_and_optional_missing_columns() -> None:
    manifest = build_manifest(training_frame())
    incoming = pd.DataFrame(
        {
            "GENDER": ["999"],
            "NUM_CONTRACTS": ["1"],
            "amount": ["150000"],
            "term": ["365"],
            "extra_note": ["kept"],
        }
    )

    result = validate_schema(incoming, manifest)

    assert result.is_compatible
    assert result.missing_features == ["optional"]
    assert result.extra_columns == ["extra_note"]
    assert result.unknown_categories == {"GENDER": ["999"]}


def test_schema_rejects_missing_critical_feature() -> None:
    manifest = build_manifest(training_frame())
    incoming = training_frame().drop(columns=[" amount ", "GB_flag"])

    result = validate_schema(incoming, manifest)

    assert not result.is_compatible
    assert "amount" in result.missing_critical_features


def test_invalid_target_is_ignored_for_metrics_without_blocking_prediction() -> None:
    manifest = build_manifest(training_frame())
    incoming = training_frame().rename(columns={" amount ": "amount"})
    incoming["GB_flag"] = ["unknown"] * len(incoming)

    result = validate_schema(incoming, manifest)

    assert result.is_compatible
    assert result.target_present
    assert not result.target_valid
    assert any("ignored for metrics" in warning for warning in result.warnings)


def test_detects_duplicate_features_with_conflicting_targets() -> None:
    frame = pd.DataFrame(
        {
            "amount": [100, 100, 200],
            "GENDER": ["515", "515", "516"],
            "GB_flag": [0, 1, 0],
        }
    )

    conflicts = find_conflicting_duplicate_features(
        frame,
        ["amount", "GENDER"],
        "GB_flag",
    )

    assert conflicts == 1
