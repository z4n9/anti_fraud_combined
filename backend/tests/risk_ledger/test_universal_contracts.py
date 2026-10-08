from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.risk_ledger.domain.canonical_schema import (
    CANONICAL_SCHEMA_V1,
    AnalysisProfile,
    EntityKind,
)
from app.risk_ledger.domain.contracts import (
    AnalysisPlanContract,
    AnalysisProgressContract,
    AnalysisResultContract,
    AnalysisStage,
    AnalysisState,
    DatasetInventory,
    DatasetSelection,
    ExportDescriptor,
    FieldInventory,
    FieldLineage,
    FieldTransformation,
    MappingConfidence,
    MappingStatus,
    PhysicalDataType,
    PlanProfileState,
    ProfilePlan,
    ProfileProgress,
    ProfileResultSummary,
    ProfileState,
    SemanticFieldMapping,
    SemanticMappingContract,
    SourceFormat,
    SourceInventoryContract,
    TimeRange,
    TransformationKind,
)


def round_trip(model):
    return type(model).model_validate_json(model.model_dump_json())


def test_canonical_schema_has_unique_fields_and_transaction_requirements() -> None:
    fields = CANONICAL_SCHEMA_V1.field_map()

    assert len(fields) == len(CANONICAL_SCHEMA_V1.fields)
    assert CANONICAL_SCHEMA_V1.contract_version == "1.0"
    assert CANONICAL_SCHEMA_V1.required_fields(
        AnalysisProfile.TRANSACTION_ANOMALY
    ) == ("transaction.timestamp", "transaction.amount")
    assert fields["client.fraud_label"].label
    assert fields["transaction.fraud_label"].label


def test_inventory_contract_round_trips_without_source_values() -> None:
    inventory = SourceInventoryContract(
        analysis_id="analysis-1",
        filename="transactions.jsonl",
        source_format=SourceFormat.JSONL,
        file_size_bytes=2048,
        datasets=(
            DatasetInventory(
                dataset_id="transactions",
                display_label="Операции",
                row_count=42,
                fields=(
                    FieldInventory(
                        source_path="created_at",
                        physical_type=PhysicalDataType.DATETIME,
                        nullable=False,
                        missing_rate=0,
                        distinct_count=42,
                    ),
                    FieldInventory(
                        source_path="amount",
                        physical_type=PhysicalDataType.NUMBER,
                        nullable=False,
                        missing_rate=0,
                        distinct_count=39,
                        likely_sensitive=True,
                    ),
                ),
                time_field_candidates=("created_at",),
            ),
        ),
    )

    restored = round_trip(inventory)

    assert restored == inventory
    assert "sample" not in inventory.model_dump_json().lower()


def test_mapping_and_plan_contracts_preserve_lineage_and_profile_gates() -> None:
    timestamp_mapping = SemanticFieldMapping(
        canonical_field="transaction.timestamp",
        display_label="Дата и время операции",
        entity=EntityKind.TRANSACTION,
        confidence=0.96,
        confidence_level=MappingConfidence.HIGH,
        status=MappingStatus.APPLIED,
        lineage=FieldLineage(
            source_dataset="transactions",
            source_path="created_at",
            canonical_field="transaction.timestamp",
            transformations=(
                FieldTransformation(kind=TransformationKind.PARSE_DATE),
            ),
            dictionary_version="dictionary-1",
            reasons=("Название и значения соответствуют дате операции",),
        ),
    )
    mapping = SemanticMappingContract(
        analysis_id="analysis-1",
        canonical_schema_version=CANONICAL_SCHEMA_V1.contract_version,
        dictionary_version="dictionary-1",
        mappings=(timestamp_mapping,),
        missing_required_fields={
            AnalysisProfile.TRANSACTION_ANOMALY: ("transaction.amount",)
        },
    )
    plan = AnalysisPlanContract(
        analysis_id="analysis-1",
        canonical_schema_version=CANONICAL_SCHEMA_V1.contract_version,
        dictionary_version="dictionary-1",
        datasets=(
            DatasetSelection(
                dataset_id="transactions",
                entity=EntityKind.TRANSACTION,
                role="primary",
            ),
        ),
        profiles=(
            ProfilePlan(
                profile=AnalysisProfile.CLIENT_RISK,
                state=PlanProfileState.SKIPPED,
                required_fields=(),
                available_fields=(),
                comparison_mode="not_applicable",
            ),
            ProfilePlan(
                profile=AnalysisProfile.TRANSACTION_ANOMALY,
                state=PlanProfileState.BLOCKED,
                required_fields=("transaction.timestamp", "transaction.amount"),
                available_fields=("transaction.timestamp",),
                blocking_reasons=("Не распознана сумма операции",),
                comparison_mode="cohort",
            ),
        ),
    )

    assert round_trip(mapping) == mapping
    assert round_trip(plan) == plan
    assert plan.runnable_profiles() == ()


def test_mapping_rejects_low_confidence_application_and_wrong_band() -> None:
    lineage = FieldLineage(
        source_dataset="clients",
        source_path="age",
        canonical_field="client.age_years",
        dictionary_version="dictionary-1",
        reasons=("Числовое поле",),
    )

    with pytest.raises(ValidationError, match="low-confidence mappings cannot be applied"):
        SemanticFieldMapping(
            canonical_field="client.age_years",
            display_label="Возраст клиента",
            entity=EntityKind.CLIENT,
            confidence=0.3,
            confidence_level=MappingConfidence.LOW,
            status=MappingStatus.APPLIED,
            lineage=lineage,
        )
    with pytest.raises(ValidationError, match="confidence_level must be high"):
        SemanticFieldMapping(
            canonical_field="client.age_years",
            display_label="Возраст клиента",
            entity=EntityKind.CLIENT,
            confidence=0.9,
            confidence_level=MappingConfidence.MEDIUM,
            status=MappingStatus.APPLIED_WITH_WARNING,
            lineage=lineage,
        )


def test_progress_supports_partial_success_and_legacy_csv_statuses() -> None:
    progress = AnalysisProgressContract(
        analysis_id="analysis-1",
        state=AnalysisState.PARTIALLY_READY,
        stage=AnalysisStage.READY,
        progress=100,
        profiles=(
            ProfileProgress(
                profile=AnalysisProfile.CLIENT_RISK,
                state=ProfileState.READY,
                stage=AnalysisStage.READY,
                progress=100,
                processed_records=100,
                total_records=100,
            ),
            ProfileProgress(
                profile=AnalysisProfile.TRANSACTION_ANOMALY,
                state=ProfileState.BLOCKED,
                stage=AnalysisStage.VALIDATING,
                progress=100,
                errors=("Нет суммы операции",),
            ),
        ),
    )
    legacy = AnalysisProgressContract.from_legacy_status(
        analysis_id="legacy-csv",
        status="completed",
        progress=100,
    )

    assert round_trip(progress) == progress
    assert legacy.state == AnalysisState.READY
    assert legacy.profiles[0].profile == AnalysisProfile.CLIENT_RISK
    assert legacy.profiles[0].state == ProfileState.READY

    with pytest.raises(ValidationError, match="partially_ready requires"):
        AnalysisProgressContract(
            analysis_id="invalid",
            state=AnalysisState.PARTIALLY_READY,
            stage=AnalysisStage.READY,
            progress=100,
            profiles=(progress.profiles[0],),
        )


def test_result_contract_round_trips_partial_profile_result() -> None:
    result = AnalysisResultContract(
        analysis_id="analysis-1",
        state="partially_ready",
        canonical_schema_version=CANONICAL_SCHEMA_V1.contract_version,
        dictionary_version="dictionary-1",
        source_period=TimeRange(
            start=datetime(2026, 8, 1, tzinfo=UTC),
            end=datetime(2026, 8, 31, 23, 59, tzinfo=UTC),
        ),
        profiles=(
            ProfileResultSummary(
                profile=AnalysisProfile.CLIENT_RISK,
                state=ProfileState.READY,
                records=100,
                requires_review=7,
                model_version="client-2.0",
            ),
            ProfileResultSummary(
                profile=AnalysisProfile.TRANSACTION_ANOMALY,
                state=ProfileState.FAILED,
                records=0,
                requires_review=0,
                errors=("Транзакционный профиль не завершён",),
            ),
        ),
        exports=(
            ExportDescriptor(
                kind="review",
                available=True,
                masked_by_default=True,
            ),
        ),
    )

    assert round_trip(result) == result


def test_contracts_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SourceInventoryContract.model_validate(
            {
                "analysis_id": "analysis-1",
                "filename": "sample.csv",
                "source_format": "csv",
                "file_size_bytes": 10,
                "datasets": [],
                "unexpected": "value",
            }
        )
