from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from app.risk_ledger.core.config import (
    CATEGORICAL_FEATURES,
    IDENTIFIER_COLUMNS,
    MISSING_CATEGORY,
    MISSING_TOKENS,
    MODEL_VERSION,
    TARGET_COLUMN,
    ZERO_MISSING_COLUMNS,
    ZERO_MISSING_PREFIXES,
)
from app.risk_ledger.domain.models import FeatureProfile, ModelManifest
from app.risk_ledger.services.client_feature_schema import resolve_client_feature_sources
from app.risk_ledger.services.semantic_dictionary import normalize_field_name


NUMERIC_INFERENCE_THRESHOLD = 0.95
MAX_STORED_CATEGORIES = 500
CANONICAL_CATEGORICAL_FEATURES = frozenset(
    {
        "client.gender",
        "client.residency",
        "client.education",
        "client.marital_status",
        "employment.nature",
    }
)
CANONICAL_ZERO_MISSING_FEATURES = frozenset({"credit.contract_count"})


def normalize_headers(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result.columns = [str(column).strip() for column in result.columns]
    if any(not column for column in result.columns):
        raise ValueError("An empty column name is not allowed.")
    duplicates = result.columns[result.columns.duplicated()].tolist()
    if duplicates:
        raise ValueError(f"Duplicate columns after normalization: {duplicates}")
    return result


def is_identifier_column(name: str) -> bool:
    lowered = name.lower()
    return lowered in IDENTIFIER_COLUMNS or lowered.endswith("_id")


def is_count_like(name: str) -> bool:
    normalized = normalize_field_name(name).upper()
    tail = normalized.rsplit("_", 1)[-1]
    return (
        name in CANONICAL_ZERO_MISSING_FEATURES
        or
        name in ZERO_MISSING_COLUMNS
        or normalized in {item.upper() for item in ZERO_MISSING_COLUMNS}
        or any(normalized.startswith(prefix) or f"_{prefix}" in normalized for prefix in ZERO_MISSING_PREFIXES)
        or tail in {item.upper() for item in ZERO_MISSING_COLUMNS}
    )


def clean_text(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    lowered = text.str.lower()
    return text.mask(lowered.isin(MISSING_TOKENS))


def parse_numeric(series: pd.Series) -> pd.Series:
    text = clean_text(series)
    normalized = text.str.replace(r"\s+", "", regex=True).str.replace(",", ".", regex=False)
    return pd.to_numeric(normalized, errors="coerce").astype("float64")


def _missing_rate(series: pd.Series) -> float:
    return float(clean_text(series).isna().mean())


def _infer_kind(name: str, series: pd.Series) -> str:
    normalized = normalize_field_name(name)
    categorical = {normalize_field_name(item) for item in CATEGORICAL_FEATURES}
    if (
        name in CANONICAL_CATEGORICAL_FEATURES
        or normalized in categorical
        or any(normalized.endswith(f"_{item}") for item in categorical)
    ):
        return "categorical"
    non_missing = clean_text(series).dropna()
    if non_missing.empty:
        return "numeric"
    numeric_ratio = float(parse_numeric(non_missing).notna().mean())
    return "numeric" if numeric_ratio >= NUMERIC_INFERENCE_THRESHOLD else "categorical"


def _safe_quantile(series: pd.Series, quantile: float) -> float | None:
    value = series.quantile(quantile)
    return None if pd.isna(value) else float(value)


def build_manifest(
    frame: pd.DataFrame,
    *,
    target_column: str = TARGET_COLUMN,
    model_version: str = MODEL_VERSION,
    random_seed: int = 42,
) -> ModelManifest:
    normalized = normalize_headers(frame)
    feature_profiles: list[FeatureProfile] = []
    dropped_features: list[str] = []
    identifier_columns: list[str] = []

    for name in normalized.columns:
        if name == target_column:
            continue
        if is_identifier_column(name):
            identifier_columns.append(name)
            continue

        series = normalized[name]
        missing_rate = _missing_rate(series)
        if missing_rate == 1.0:
            dropped_features.append(name)
            continue

        kind = _infer_kind(name, series)
        if kind == "categorical":
            values = clean_text(series).dropna().astype(str)
            categories = sorted(values.unique().tolist())[:MAX_STORED_CATEGORIES]
            feature_profiles.append(
                FeatureProfile(
                    name=name,
                    kind="categorical",
                    missing_strategy="category",
                    training_missing_rate=missing_rate,
                    categories=categories,
                )
            )
            continue

        numeric = parse_numeric(series)
        numeric = numeric.mask((numeric < 0) & is_count_like(name))
        missing_strategy = "zero" if is_count_like(name) else "preserve"
        feature_profiles.append(
            FeatureProfile(
                name=name,
                kind="numeric",
                missing_strategy=missing_strategy,
                training_missing_rate=float(numeric.isna().mean()),
                q01=_safe_quantile(numeric, 0.01),
                q99=_safe_quantile(numeric, 0.99),
            )
        )

    feature_columns = [profile.name for profile in feature_profiles]
    categorical = [profile.name for profile in feature_profiles if profile.kind == "categorical"]
    numerical = [profile.name for profile in feature_profiles if profile.kind == "numeric"]
    critical = [
        profile.name
        for profile in feature_profiles
        if profile.training_missing_rate <= 0.05
    ]

    return ModelManifest(
        schema_version=1,
        model_version=model_version,
        target_column=target_column,
        feature_columns=feature_columns,
        categorical_features=categorical,
        numerical_features=numerical,
        dropped_features=dropped_features,
        identifier_columns=identifier_columns,
        critical_features=critical,
        feature_profiles=feature_profiles,
        review_threshold=0.5,
        risk_boundaries={"medium": 0.25, "high": 0.5, "critical": 0.75},
        random_seed=random_seed,
        training_rows=len(normalized),
        target_rate=0.0,
    )


def transform_features(frame: pd.DataFrame, manifest: ModelManifest) -> pd.DataFrame:
    normalized = normalize_headers(frame)
    profiles = manifest.profile_map()
    transformed: dict[str, pd.Series] = {}
    resolved = resolve_client_feature_sources(
        normalized,
        manifest.feature_columns,
        manifest.feature_sources,
    )

    for name in manifest.feature_columns:
        source = (
            normalized[resolved[name]]
            if name in resolved
            else pd.Series(pd.NA, index=normalized.index, dtype="string")
        )
        profile = profiles[name]
        if profile.kind == "categorical":
            transformed[name] = clean_text(source).fillna(MISSING_CATEGORY).astype(str)
            continue

        numeric = parse_numeric(source)
        if is_count_like(name):
            numeric = numeric.mask(numeric < 0)
        if profile.missing_strategy == "zero":
            numeric = numeric.fillna(0.0)
        transformed[name] = numeric.astype("float64")

    return pd.DataFrame(transformed, index=normalized.index)


def ensure_binary_target(series: pd.Series) -> pd.Series:
    target = parse_numeric(series)
    if target.isna().any():
        raise ValueError("Target contains missing or non-numeric values.")
    unique = set(target.unique().tolist())
    if not unique.issubset({0.0, 1.0}) or len(unique) != 2:
        raise ValueError("Target must contain both binary values 0 and 1.")
    return target.astype("int8")


def remove_exact_duplicates(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    duplicate_count = int(frame.duplicated().sum())
    return frame.drop_duplicates().reset_index(drop=True), duplicate_count


def find_conflicting_duplicate_features(
    frame: pd.DataFrame,
    feature_columns: Iterable[str],
    target_column: str,
) -> int:
    columns = list(feature_columns)
    if not columns:
        return 0
    hashes = pd.util.hash_pandas_object(frame[columns].astype("string"), index=False)
    target_counts = frame.groupby(hashes, sort=False)[target_column].nunique(dropna=False)
    return int((target_counts > 1).sum())
