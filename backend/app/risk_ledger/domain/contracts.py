from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.risk_ledger.domain.canonical_schema import AnalysisProfile, EntityKind


CONTRACT_VERSION = "1.0"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class VersionedContract(ContractModel):
    contract_version: Literal["1.0"] = CONTRACT_VERSION


class SourceFormat(StrEnum):
    CSV = "csv"
    JSON = "json"
    JSONL = "jsonl"
    NDJSON = "ndjson"
    SQL_DUMP = "sql_dump"
    SQLITE = "sqlite"
    BSON = "bson"


class PhysicalDataType(StrEnum):
    NULL = "null"
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    OBJECT = "object"
    ARRAY = "array"
    MIXED = "mixed"


class RelationshipKind(StrEnum):
    PRIMARY_KEY = "primary_key"
    FOREIGN_KEY = "foreign_key"
    INFERRED_REFERENCE = "inferred_reference"


class MappingConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MappingStatus(StrEnum):
    APPLIED = "applied"
    APPLIED_WITH_WARNING = "applied_with_warning"
    UNUSED = "unused"
    BLOCKED = "blocked"


class TransformationKind(StrEnum):
    TRIM = "trim"
    NORMALIZE_MISSING = "normalize_missing"
    PARSE_NUMBER = "parse_number"
    PARSE_DATE = "parse_date"
    NORMALIZE_CATEGORY = "normalize_category"
    HASH_IDENTIFIER = "hash_identifier"
    DERIVE = "derive"


class AnalysisStage(StrEnum):
    UPLOADED = "uploaded"
    INSPECTING = "inspecting"
    MAPPING = "mapping"
    PLANNING = "planning"
    VALIDATING = "validating"
    TRANSFORMING = "transforming"
    CLIENT_SCORING = "client_scoring"
    TRANSACTION_FEATURES = "transaction_features"
    TRANSACTION_SCORING = "transaction_scoring"
    RELATIONSHIPS = "relationships"
    EXPLAINING = "explaining"
    EXPORTING = "exporting"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    PARTIALLY_READY = "partially_ready"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    DELETED = "deleted"


class ProfileState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class PlanProfileState(StrEnum):
    PLANNED = "planned"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class FieldInventory(ContractModel):
    source_path: str
    display_label: str | None = None
    physical_type: PhysicalDataType
    nullable: bool
    missing_rate: float = Field(ge=0, le=1)
    distinct_count: int | None = Field(default=None, ge=0)
    likely_identifier: bool = False
    likely_sensitive: bool = False
    time_min: datetime | None = None
    time_max: datetime | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> "FieldInventory":
        if (
            self.time_min is not None
            and self.time_max is not None
            and self.time_max < self.time_min
        ):
            raise ValueError("field time_max must not precede time_min")
        return self

class RelationshipCandidate(ContractModel):
    from_dataset: str
    from_field: str
    to_dataset: str
    to_field: str
    kind: RelationshipKind
    confidence: float = Field(ge=0, le=1)


class IndexInventory(ContractModel):
    name: str
    fields: tuple[str, ...]
    unique: bool = False


class DatasetInventory(ContractModel):
    dataset_id: str
    display_label: str
    row_count: int = Field(ge=0)
    fields: tuple[FieldInventory, ...]
    primary_key_candidates: tuple[str, ...] = ()
    time_field_candidates: tuple[str, ...] = ()
    indexes: tuple[IndexInventory, ...] = ()


class SourceInventoryContract(VersionedContract):
    analysis_id: str
    filename: str
    source_format: SourceFormat
    file_size_bytes: int = Field(ge=0)
    datasets: tuple[DatasetInventory, ...]
    relationships: tuple[RelationshipCandidate, ...] = ()
    warnings: tuple[str, ...] = ()


class FieldTransformation(ContractModel):
    kind: TransformationKind
    parameters: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict
    )


class FieldLineage(ContractModel):
    source_dataset: str
    source_path: str
    canonical_field: str
    transformations: tuple[FieldTransformation, ...] = ()
    dictionary_version: str
    reasons: tuple[str, ...]


class SemanticFieldMapping(ContractModel):
    canonical_field: str
    display_label: str
    entity: EntityKind
    confidence: float = Field(ge=0, le=1)
    confidence_level: MappingConfidence
    status: MappingStatus
    lineage: FieldLineage
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_confidence_band(self) -> "SemanticFieldMapping":
        expected = (
            MappingConfidence.HIGH
            if self.confidence >= 0.85
            else MappingConfidence.MEDIUM
            if self.confidence >= 0.6
            else MappingConfidence.LOW
        )
        if self.confidence_level != expected:
            raise ValueError(
                f"confidence_level must be {expected.value} for score {self.confidence}"
            )
        if self.confidence_level == MappingConfidence.LOW and self.status in {
            MappingStatus.APPLIED,
            MappingStatus.APPLIED_WITH_WARNING,
        }:
            raise ValueError("low-confidence mappings cannot be applied")
        return self


class SemanticMappingContract(VersionedContract):
    analysis_id: str
    canonical_schema_version: str
    dictionary_version: str
    mappings: tuple[SemanticFieldMapping, ...]
    unmapped_source_fields: tuple[str, ...] = ()
    missing_required_fields: dict[AnalysisProfile, tuple[str, ...]] = Field(
        default_factory=dict
    )


class TimeRange(ContractModel):
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="after")
    def validate_order(self) -> "TimeRange":
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("time range end must not precede start")
        return self


class DatasetSelection(ContractModel):
    dataset_id: str
    entity: EntityKind
    role: Literal["primary", "related"]
    join_to_dataset: str | None = None
    join_from_field: str | None = None
    join_to_field: str | None = None


class ProfilePlan(ContractModel):
    profile: AnalysisProfile
    state: PlanProfileState
    required_fields: tuple[str, ...]
    available_fields: tuple[str, ...]
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    comparison_mode: Literal["not_applicable", "cohort", "historical", "mixed"]

    @model_validator(mode="after")
    def validate_blocking_reasons(self) -> "ProfilePlan":
        if self.state == PlanProfileState.BLOCKED and not self.blocking_reasons:
            raise ValueError("blocked profile plans require a blocking reason")
        if self.state == PlanProfileState.PLANNED and self.blocking_reasons:
            raise ValueError("planned profiles cannot have blocking reasons")
        return self


class AnalysisPlanContract(VersionedContract):
    analysis_id: str
    canonical_schema_version: str
    dictionary_version: str
    datasets: tuple[DatasetSelection, ...]
    time_range: TimeRange | None = None
    profiles: tuple[ProfilePlan, ...]
    warnings: tuple[str, ...] = ()

    def runnable_profiles(self) -> tuple[AnalysisProfile, ...]:
        return tuple(
            item.profile
            for item in self.profiles
            if item.state == PlanProfileState.PLANNED
        )


class ProfileProgress(ContractModel):
    profile: AnalysisProfile
    state: ProfileState
    stage: AnalysisStage
    progress: int = Field(ge=0, le=100)
    processed_records: int = Field(default=0, ge=0)
    total_records: int | None = Field(default=None, ge=0)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class AnalysisProgressContract(VersionedContract):
    analysis_id: str
    state: AnalysisState
    stage: AnalysisStage
    progress: int = Field(ge=0, le=100)
    processed_records: int = Field(default=0, ge=0)
    total_records: int | None = Field(default=None, ge=0)
    profiles: tuple[ProfileProgress, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    can_cancel: bool = False

    @model_validator(mode="after")
    def validate_state(self) -> "AnalysisProgressContract":
        ready = sum(profile.state == ProfileState.READY for profile in self.profiles)
        unsuccessful = sum(
            profile.state in {ProfileState.BLOCKED, ProfileState.FAILED}
            for profile in self.profiles
        )
        if self.state == AnalysisState.PARTIALLY_READY and not (
            ready > 0 and unsuccessful > 0
        ):
            raise ValueError(
                "partially_ready requires at least one ready and one blocked or failed profile"
            )
        if self.state == AnalysisState.READY and any(
            profile.state not in {ProfileState.READY, ProfileState.SKIPPED}
            for profile in self.profiles
        ):
            raise ValueError("ready analysis contains an unfinished profile")
        return self

    @classmethod
    def from_legacy_status(
        cls,
        *,
        analysis_id: str,
        status: Literal["queued", "validating", "predicting", "completed", "failed"],
        progress: int,
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
    ) -> "AnalysisProgressContract":
        mapping = {
            "queued": (AnalysisState.QUEUED, AnalysisStage.UPLOADED),
            "validating": (AnalysisState.RUNNING, AnalysisStage.VALIDATING),
            "predicting": (AnalysisState.RUNNING, AnalysisStage.CLIENT_SCORING),
            "completed": (AnalysisState.READY, AnalysisStage.READY),
            "failed": (AnalysisState.FAILED, AnalysisStage.FAILED),
        }
        state, stage = mapping[status]
        profile_state = {
            AnalysisState.QUEUED: ProfileState.PENDING,
            AnalysisState.RUNNING: ProfileState.RUNNING,
            AnalysisState.READY: ProfileState.READY,
            AnalysisState.FAILED: ProfileState.FAILED,
        }[state]
        return cls(
            analysis_id=analysis_id,
            state=state,
            stage=stage,
            progress=progress,
            profiles=(
                ProfileProgress(
                    profile=AnalysisProfile.CLIENT_RISK,
                    state=profile_state,
                    stage=stage,
                    progress=progress,
                    errors=tuple(errors or ()),
                    warnings=tuple(warnings or ()),
                ),
            ),
            warnings=tuple(warnings or ()),
            errors=tuple(errors or ()),
            can_cancel=state in {AnalysisState.QUEUED, AnalysisState.RUNNING},
        )


class ProfileResultSummary(ContractModel):
    profile: AnalysisProfile
    state: ProfileState
    records: int = Field(ge=0)
    requires_review: int = Field(ge=0)
    model_version: str | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_counts(self) -> "ProfileResultSummary":
        if self.requires_review > self.records:
            raise ValueError("requires_review cannot exceed records")
        return self


class ExportDescriptor(ContractModel):
    kind: Literal[
        "full",
        "review",
        "transactions",
        "relationships",
        "mapping_quality",
        "technical_plan",
    ]
    available: bool
    masked_by_default: bool


class AnalysisResultContract(VersionedContract):
    analysis_id: str
    state: Literal["ready", "partially_ready", "failed"]
    canonical_schema_version: str
    dictionary_version: str
    source_period: TimeRange | None = None
    profiles: tuple[ProfileResultSummary, ...]
    exports: tuple[ExportDescriptor, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_partial_result(self) -> "AnalysisResultContract":
        ready = sum(profile.state == ProfileState.READY for profile in self.profiles)
        unsuccessful = sum(
            profile.state in {ProfileState.BLOCKED, ProfileState.FAILED}
            for profile in self.profiles
        )
        if self.state == "partially_ready" and not (ready and unsuccessful):
            raise ValueError(
                "partially_ready result requires successful and unsuccessful profiles"
            )
        return self
