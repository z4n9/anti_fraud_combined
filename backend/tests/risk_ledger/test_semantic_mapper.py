from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.risk_ledger.domain.canonical_schema import AnalysisProfile
from app.risk_ledger.domain.contracts import (
    DatasetInventory,
    FieldInventory,
    MappingConfidence,
    MappingStatus,
    PhysicalDataType,
    SourceFormat,
    SourceInventoryContract,
)
from app.risk_ledger.services.semantic_dictionary import (
    SemanticDictionary,
    normalize_field_name,
)
from app.risk_ledger.services.semantic_mapper import SemanticSchemaMapper


def inventory(
    *fields: FieldInventory,
    dataset_id: str = "records",
    label: str = "Данные источника",
) -> SourceInventoryContract:
    return SourceInventoryContract(
        analysis_id="analysis-1",
        filename="source.jsonl",
        source_format=SourceFormat.JSONL,
        file_size_bytes=100,
        datasets=(
            DatasetInventory(
                dataset_id=dataset_id,
                display_label=label,
                row_count=100,
                fields=fields,
            ),
        ),
    )


def field(
    name: str,
    physical_type: PhysicalDataType,
    *,
    missing_rate: float = 0,
    distinct_count: int | None = 80,
    identifier: bool = False,
) -> FieldInventory:
    return FieldInventory(
        source_path=name,
        physical_type=physical_type,
        nullable=missing_rate > 0,
        missing_rate=missing_rate,
        distinct_count=distinct_count,
        likely_identifier=identifier,
    )


def mapping_by_canonical(result):
    return {mapping.canonical_field: mapping for mapping in result.mappings}


def test_normalizes_case_camel_case_spaces_and_unicode() -> None:
    assert normalize_field_name(" TransactionAmount ") == "transaction_amount"
    assert normalize_field_name("Сумма операции") == "сумма_операции"
    assert normalize_field_name("Ёлка---Поле") == "елка_поле"


def test_maps_renamed_transaction_fields_with_high_confidence() -> None:
    source = inventory(
        field("operation_id", PhysicalDataType.STRING, identifier=True),
        field("operation_datetime", PhysicalDataType.DATETIME),
        field("payment_sum", PhysicalDataType.NUMBER),
        field("currency_code", PhysicalDataType.STRING, distinct_count=3),
        label="Операции за месяц",
    )

    result = SemanticSchemaMapper().map(source)
    mappings = mapping_by_canonical(result)

    assert mappings["transaction.timestamp"].status == MappingStatus.APPLIED
    assert mappings["transaction.amount"].status == MappingStatus.APPLIED
    assert mappings["transaction.record_id"].confidence_level == MappingConfidence.HIGH
    assert mappings["transaction.currency"].display_label == "Валюта операции"
    assert (
        result.missing_required_fields[AnalysisProfile.TRANSACTION_ANOMALY] == ()
    )


def test_maps_current_client_dataset_aliases_without_using_values() -> None:
    source = inventory(
        field(" total_amount_kzt ", PhysicalDataType.NUMBER),
        field("term", PhysicalDataType.INTEGER),
        field("AGE", PhysicalDataType.INTEGER),
        field("GENDER", PhysicalDataType.INTEGER, distinct_count=2),
        field("EMPLOYMENTNATURE", PhysicalDataType.STRING, distinct_count=5),
        field("overdueamount", PhysicalDataType.NUMBER),
        field("outstandingamount", PhysicalDataType.NUMBER),
        field("DTI3M", PhysicalDataType.NUMBER),
        field("GB_flag", PhysicalDataType.INTEGER, distinct_count=2),
        label="Кредитные анкеты",
    )

    result = SemanticSchemaMapper().map(source)
    applied = {
        mapping.canonical_field
        for mapping in result.mappings
        if mapping.status == MappingStatus.APPLIED
    }

    assert {
        "credit.requested_amount",
        "credit.term_days",
        "client.age_years",
        "client.gender",
        "employment.nature",
        "credit.overdue_amount",
        "credit.outstanding_amount",
        "credit.debt_to_income",
        "client.fraud_label",
    }.issubset(applied)


def test_ambiguous_generic_amount_is_not_applied_silently() -> None:
    result = SemanticSchemaMapper().map(
        inventory(field("amount", PhysicalDataType.NUMBER))
    )

    assert len(result.mappings) == 1
    ambiguous = result.mappings[0]
    assert ambiguous.confidence_level == MappingConfidence.LOW
    assert ambiguous.status == MappingStatus.UNUSED
    assert any("неоднозначно" in warning.lower() for warning in ambiguous.warnings)
    assert "transaction.amount" in result.missing_required_fields[
        AnalysisProfile.TRANSACTION_ANOMALY
    ]


def test_unknown_and_wrongly_typed_required_fields_do_not_pass() -> None:
    result = SemanticSchemaMapper().map(
        inventory(
            field("mystery_payload", PhysicalDataType.OBJECT),
            field("operation_datetime", PhysicalDataType.INTEGER),
            field("payment_sum", PhysicalDataType.NUMBER),
            label="Операции",
        )
    )
    mappings = mapping_by_canonical(result)

    assert "records.mystery_payload" in result.unmapped_source_fields
    assert mappings["transaction.timestamp"].status == MappingStatus.UNUSED
    assert "transaction.timestamp" in result.missing_required_fields[
        AnalysisProfile.TRANSACTION_ANOMALY
    ]
    assert mappings["transaction.amount"].status == MappingStatus.APPLIED


def test_medium_confidence_optional_field_is_applied_with_warning() -> None:
    dictionary = SemanticDictionary()
    dictionary.record_confirmation(
        alias="custom_age_metric",
        canonical_field="client.age_years",
        physical_type=PhysicalDataType.NUMBER,
        accepted=True,
    )
    result = SemanticSchemaMapper(dictionary).map(
        inventory(field("custom_age_metric", PhysicalDataType.NUMBER))
    )
    mapping = mapping_by_canonical(result)["client.age_years"]

    assert mapping.confidence_level == MappingConfidence.MEDIUM
    assert mapping.status == MappingStatus.APPLIED_WITH_WARNING
    assert mapping.warnings


def test_dictionary_promotes_only_after_repeated_confirmation_and_persists(
    tmp_path: Path,
) -> None:
    path = tmp_path / "semantic-dictionary.json"
    dictionary = SemanticDictionary(path)
    for _ in range(2):
        entry = dictionary.record_confirmation(
            alias="event_moment",
            canonical_field="transaction.timestamp",
            physical_type=PhysicalDataType.DATETIME,
            transformations=("parse_date",),
            accepted=True,
        )
        assert entry.status == "candidate"

    before_promotion = SemanticSchemaMapper(dictionary).map(
        inventory(field("event_moment", PhysicalDataType.DATETIME))
    )
    assert mapping_by_canonical(before_promotion)[
        "transaction.timestamp"
    ].status == MappingStatus.UNUSED

    promoted = dictionary.record_confirmation(
        alias="event_moment",
        canonical_field="transaction.timestamp",
        physical_type=PhysicalDataType.DATETIME,
        transformations=("parse_date",),
        accepted=True,
    )
    assert promoted.status == "trusted"

    restored = SemanticDictionary(path)
    after_promotion = SemanticSchemaMapper(restored).map(
        inventory(field("event_moment", PhysicalDataType.DATETIME))
    )
    assert mapping_by_canonical(after_promotion)[
        "transaction.timestamp"
    ].status == MappingStatus.APPLIED
    assert restored.version_label == dictionary.version_label


def test_dictionary_rejection_and_rollback_are_auditable(tmp_path: Path) -> None:
    path = tmp_path / "semantic-dictionary.json"
    dictionary = SemanticDictionary(path)
    entry = dictionary.record_confirmation(
        alias="event_moment",
        canonical_field="transaction.timestamp",
        physical_type=PhysicalDataType.DATETIME,
        accepted=False,
    )
    assert entry.status == "rejected"
    assert entry.rejections == 1

    rolled_back = dictionary.rollback("event_moment", "transaction.timestamp")
    assert rolled_back.status == "candidate"
    assert rolled_back.successes == 0
    assert rolled_back.rejections == 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == dictionary.version
    assert "event_moment" in path.read_text(encoding="utf-8")


def test_dictionary_does_not_trust_alias_with_conflicting_physical_types() -> None:
    dictionary = SemanticDictionary()
    for physical_type in (
        PhysicalDataType.DATETIME,
        PhysicalDataType.STRING,
        PhysicalDataType.DATETIME,
        PhysicalDataType.DATETIME,
    ):
        entry = dictionary.record_confirmation(
            alias="event_moment",
            canonical_field="transaction.timestamp",
            physical_type=physical_type,
            accepted=True,
        )

    assert entry.status == "candidate"
    assert entry.physical_types == ["datetime", "string"]


def test_dictionary_rejects_personal_values_and_unknown_canonical_fields(
    tmp_path: Path,
) -> None:
    dictionary = SemanticDictionary(tmp_path / "dictionary.json")
    with pytest.raises(ValueError, match="safe field aliases"):
        dictionary.record_confirmation(
            alias="person@example.com",
            canonical_field="client.record_id",
            physical_type=PhysicalDataType.STRING,
            accepted=True,
        )
    with pytest.raises(ValueError, match="Unknown canonical"):
        dictionary.record_confirmation(
            alias="safe_field_name",
            canonical_field="client.secret_value",
            physical_type=PhysicalDataType.STRING,
            accepted=True,
        )
    assert not (tmp_path / "dictionary.json").exists()
