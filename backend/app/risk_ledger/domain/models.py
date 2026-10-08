from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


FeatureKind = Literal["numeric", "categorical"]
MissingStrategy = Literal["zero", "preserve", "category"]


@dataclass(slots=True)
class FeatureProfile:
    name: str
    kind: FeatureKind
    missing_strategy: MissingStrategy
    training_missing_rate: float
    categories: list[str] = field(default_factory=list)
    q01: float | None = None
    q99: float | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeatureProfile":
        return cls(**payload)


@dataclass(slots=True)
class ModelManifest:
    schema_version: int
    model_version: str
    target_column: str
    feature_columns: list[str]
    categorical_features: list[str]
    numerical_features: list[str]
    dropped_features: list[str]
    identifier_columns: list[str]
    critical_features: list[str]
    feature_profiles: list[FeatureProfile]
    review_threshold: float
    risk_boundaries: dict[str, float]
    random_seed: int
    training_rows: int
    target_rate: float
    model_filename: str = "model.cbm"
    model_sha256: str = ""
    canonical_schema_version: str | None = None
    feature_sources: dict[str, list[str]] = field(default_factory=dict)
    feature_labels: dict[str, dict[str, str]] = field(default_factory=dict)
    shap_dictionary_version: str | None = None
    training_source_sha256: str = ""

    def profile_map(self) -> dict[str, FeatureProfile]:
        return {profile.name: profile for profile in self.feature_profiles}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ModelManifest":
        data = dict(payload)
        data["feature_profiles"] = [
            FeatureProfile.from_dict(item) for item in data.get("feature_profiles", [])
        ]
        return cls(**data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "ModelManifest":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


@dataclass(slots=True)
class SchemaValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_features: list[str] = field(default_factory=list)
    missing_critical_features: list[str] = field(default_factory=list)
    extra_columns: list[str] = field(default_factory=list)
    unknown_categories: dict[str, list[str]] = field(default_factory=dict)
    target_present: bool = False
    target_valid: bool = False

    @property
    def is_compatible(self) -> bool:
        return not self.errors


RiskLevel = Literal["low", "medium", "high", "critical"]


@dataclass(slots=True)
class ExplanationFactor:
    feature: str
    value: str | float | int | None
    contribution: float
    direction: Literal["increases_risk", "decreases_risk"]


@dataclass(slots=True)
class AnalysisMetrics:
    available: bool
    threshold: float
    gini: float | None = None
    ks: float | None = None
    accuracy: float | None = None
    precision: float | None = None
    recall: float | None = None
    roc_auc: float | None = None
    pr_auc: float | None = None
    confusion_matrix: list[list[int]] | None = None
    unavailable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PredictionBatch:
    rows: list[dict[str, Any]]
    probabilities: list[float]
    threshold: float


@dataclass(slots=True)
class ResultPage:
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    threshold: float


@dataclass(slots=True)
class ProbabilityBin:
    from_value: float
    to_value: float
    count: int


@dataclass(slots=True)
class RiskDistribution:
    analysis_id: str
    threshold: float
    risk_counts: dict[RiskLevel, int]
    probability_histogram: list[ProbabilityBin]
