from __future__ import annotations

import pandas as pd

from app.risk_ledger.core.config import (
    DRIFT_MISSING_RATE_DELTA,
    MAX_OPTIONAL_MISSING_RATIO,
    MISSING_CATEGORY,
)
from app.risk_ledger.domain.models import ModelManifest, SchemaValidationResult
from app.risk_ledger.services.client_feature_schema import resolve_client_feature_sources
from app.risk_ledger.services.preprocessing import clean_text, ensure_binary_target, normalize_headers


def validate_schema(frame: pd.DataFrame, manifest: ModelManifest) -> SchemaValidationResult:
    normalized = normalize_headers(frame)
    result = SchemaValidationResult()
    available = set(normalized.columns)
    expected = set(manifest.feature_columns)
    resolved = resolve_client_feature_sources(
        normalized,
        manifest.feature_columns,
        manifest.feature_sources,
    )
    resolved_features = set(resolved)
    used_source_columns = set(resolved.values())

    result.target_present = manifest.target_column in available
    result.extra_columns = sorted(
        available
        - used_source_columns
        - {manifest.target_column}
        - set(manifest.identifier_columns)
    )
    result.missing_features = sorted(expected - resolved_features)
    result.missing_critical_features = sorted(
        set(manifest.critical_features) - resolved_features
    )

    if result.missing_critical_features:
        result.errors.append(
            "Missing critical features: " + ", ".join(result.missing_critical_features)
        )

    missing_ratio = len(result.missing_features) / max(len(expected), 1)
    if missing_ratio > MAX_OPTIONAL_MISSING_RATIO:
        result.errors.append(
            f"Missing feature ratio {missing_ratio:.1%} exceeds "
            f"the allowed {MAX_OPTIONAL_MISSING_RATIO:.1%}."
        )
    elif result.missing_features:
        result.warnings.append(
            "Optional features will be imputed: " + ", ".join(result.missing_features)
        )

    if result.extra_columns:
        result.warnings.append(
            "Extra columns will be preserved but ignored by the model: "
            + ", ".join(result.extra_columns)
        )

    profiles = manifest.profile_map()
    for name in manifest.feature_columns:
        source_name = resolved.get(name)
        if source_name is None:
            continue
        profile = profiles[name]
        missing_rate = float(clean_text(normalized[source_name]).isna().mean())
        if missing_rate > profile.training_missing_rate + DRIFT_MISSING_RATE_DELTA:
            result.warnings.append(
                f"Missing rate drift for {name}: {missing_rate:.1%} versus "
                f"{profile.training_missing_rate:.1%} in training."
            )

        if profile.kind != "categorical" or not profile.categories:
            continue
        values = set(
            clean_text(normalized[source_name]).dropna().astype(str).unique().tolist()
        )
        unknown = sorted(values - set(profile.categories))
        if unknown:
            result.unknown_categories[name] = unknown[:20]
            result.warnings.append(
                f"Unknown categories in {name}: {', '.join(unknown[:5])}"
            )

    if result.target_present:
        try:
            ensure_binary_target(normalized[manifest.target_column])
            result.target_valid = True
        except ValueError as error:
            result.warnings.append(
                f"Target will be ignored for metrics: {error}"
            )

    return result
