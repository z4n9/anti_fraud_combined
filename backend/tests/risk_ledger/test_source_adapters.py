from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.risk_ledger.domain.contracts import PhysicalDataType, SourceFormat
from app.risk_ledger.services.source_adapters import (
    CsvSourceAdapter,
    JsonLinesSourceAdapter,
    JsonSourceAdapter,
    SourceAdapterError,
    SourceAdapterRegistry,
    SourceRecordLimitError,
)


def inspect(adapter, path: Path, source_format: SourceFormat, **kwargs):
    return adapter.inspect(
        path,
        analysis_id="analysis-1",
        filename=path.name,
        source_format=source_format,
        batch_size=2,
        **kwargs,
    )


def field_map(inventory):
    return {field.source_path: field for field in inventory.datasets[0].fields}


def test_equivalent_tabular_sources_produce_the_same_dataset_inventory(
    tmp_path: Path,
) -> None:
    records = [
        {"id": 1, "amount": 10.5, "occurred_at": "2026-09-01T10:00:00Z"},
        {"id": 2, "amount": 20.0, "occurred_at": "2026-09-02T11:30:00Z"},
        {"id": 3, "amount": 30.25, "occurred_at": "2026-09-03T12:15:00Z"},
    ]
    csv_path = tmp_path / "records.csv"
    csv_path.write_text(
        "id;amount;occurred_at\n"
        "1;10.5;2026-09-01T10:00:00Z\n"
        "2;20.0;2026-09-02T11:30:00Z\n"
        "3;30.25;2026-09-03T12:15:00Z\n",
        encoding="utf-8",
    )
    json_path = tmp_path / "records.json"
    json_path.write_text(json.dumps(records), encoding="utf-8")
    jsonl_path = tmp_path / "records.jsonl"
    jsonl_path.write_text(
        "\n".join(json.dumps(record) for record in records),
        encoding="utf-8",
    )
    ndjson_path = tmp_path / "records.ndjson"
    ndjson_path.write_bytes(jsonl_path.read_bytes())

    datasets = [
        inspect(CsvSourceAdapter(), csv_path, SourceFormat.CSV).datasets[0],
        inspect(JsonSourceAdapter(), json_path, SourceFormat.JSON).datasets[0],
        inspect(
            JsonLinesSourceAdapter(), jsonl_path, SourceFormat.JSONL
        ).datasets[0],
        inspect(
            JsonLinesSourceAdapter(), ndjson_path, SourceFormat.NDJSON
        ).datasets[0],
    ]

    assert all(dataset == datasets[0] for dataset in datasets[1:])
    assert datasets[0].row_count == 3
    assert datasets[0].primary_key_candidates == ("id",)
    assert datasets[0].time_field_candidates == ("occurred_at",)
    occurred = next(
        field for field in datasets[0].fields if field.source_path == "occurred_at"
    )
    assert occurred.time_min == datetime(2026, 9, 1, 10, tzinfo=UTC)
    assert occurred.time_max == datetime(2026, 9, 3, 12, 15, tzinfo=UTC)


def test_csv_supports_windows_1251_and_rejects_inconsistent_width(
    tmp_path: Path,
) -> None:
    encoded = tmp_path / "clients.csv"
    encoded.write_bytes("id;город\n1;Алматы\n".encode("cp1251"))
    inventory = inspect(CsvSourceAdapter(), encoded, SourceFormat.CSV)
    assert [field.source_path for field in inventory.datasets[0].fields] == [
        "id",
        "город",
    ]

    malformed = tmp_path / "malformed.csv"
    malformed.write_text("id;amount\n1;20;extra\n", encoding="utf-8")
    with pytest.raises(SourceAdapterError, match="width"):
        inspect(CsvSourceAdapter(), malformed, SourceFormat.CSV)


def test_empty_csv_keeps_declared_schema(tmp_path: Path) -> None:
    source = tmp_path / "empty.csv"
    source.write_text("id;amount\n", encoding="utf-8")
    inventory = inspect(CsvSourceAdapter(), source, SourceFormat.CSV)

    assert inventory.datasets[0].row_count == 0
    assert [field.source_path for field in inventory.datasets[0].fields] == [
        "id",
        "amount",
    ]


def test_json_flattens_nested_objects_without_expanding_arrays(
    tmp_path: Path,
) -> None:
    source = tmp_path / "documents.json"
    source.write_text(
        json.dumps(
            [
                {
                    "id": "a",
                    "client": {"city": "A", "details": {"tier": "gold"}},
                    "tags": ["new", "mobile"],
                },
                {"id": "b", "client": {"details": {"tier": "silver"}}},
            ]
        ),
        encoding="utf-8",
    )

    inventory = inspect(
        JsonSourceAdapter(),
        source,
        SourceFormat.JSON,
        max_depth=1,
    )
    fields = field_map(inventory)

    assert list(fields) == ["id", "client.city", "client.details", "tags"]
    assert fields["client.details"].physical_type == PhysicalDataType.OBJECT
    assert fields["tags"].physical_type == PhysicalDataType.ARRAY
    assert fields["client.city"].nullable is True
    assert fields["client.city"].missing_rate == 0.5


def test_heterogeneous_json_lines_builds_union_schema_and_tracks_missing_values(
    tmp_path: Path,
) -> None:
    source = tmp_path / "mixed.jsonl"
    source.write_text(
        '{"id": 1, "amount": 10}\n'
        '{"id": 2, "amount": null, "note": "manual"}\n'
        '{"id": 3, "amount": "unknown"}\n',
        encoding="utf-8",
    )

    inventory = inspect(
        JsonLinesSourceAdapter(),
        source,
        SourceFormat.JSONL,
    )
    fields = field_map(inventory)

    assert inventory.datasets[0].row_count == 3
    assert fields["amount"].physical_type == PhysicalDataType.MIXED
    assert fields["amount"].missing_rate == pytest.approx(1 / 3)
    assert fields["note"].missing_rate == pytest.approx(2 / 3)


def test_json_lines_reports_physical_line_for_invalid_document(tmp_path: Path) -> None:
    source = tmp_path / "invalid.ndjson"
    source.write_text('{"id": 1}\n\n{"id": }\n', encoding="utf-8")

    with pytest.raises(SourceAdapterError, match="line 3"):
        inspect(JsonLinesSourceAdapter(), source, SourceFormat.NDJSON)


@pytest.mark.parametrize(
    ("adapter", "filename", "content", "source_format"),
    [
        (
            CsvSourceAdapter(),
            "data.csv",
            "id;value\n1;a\n2;b\n",
            SourceFormat.CSV,
        ),
        (
            JsonSourceAdapter(),
            "data.json",
            '[{"id": 1}, {"id": 2}]',
            SourceFormat.JSON,
        ),
        (
            JsonLinesSourceAdapter(),
            "data.jsonl",
            '{"id": 1}\n{"id": 2}\n',
            SourceFormat.JSONL,
        ),
    ],
)
def test_all_adapters_enforce_logical_record_limit(
    tmp_path: Path,
    adapter,
    filename: str,
    content: str,
    source_format: SourceFormat,
) -> None:
    source = tmp_path / filename
    source.write_text(content, encoding="utf-8")
    with pytest.raises(SourceRecordLimitError, match="more than 1"):
        inspect(adapter, source, source_format, max_records=1)


def test_registry_contains_all_agreed_source_adapters() -> None:
    registry = SourceAdapterRegistry()
    assert registry.supports(SourceFormat.CSV)
    assert registry.supports(SourceFormat.JSON)
    assert registry.supports(SourceFormat.JSONL)
    assert registry.supports(SourceFormat.NDJSON)
    assert registry.supports(SourceFormat.SQLITE)
    assert registry.supports(SourceFormat.SQL_DUMP)
    assert registry.supports(SourceFormat.BSON)


def test_adapters_yield_bounded_batches_and_check_cancellation(tmp_path: Path) -> None:
    source = tmp_path / "records.jsonl"
    source.write_text(
        "\n".join(json.dumps({"id": index}) for index in range(5)),
        encoding="utf-8",
    )
    checks: list[int] = []

    batches = list(
        JsonLinesSourceAdapter().iter_batches(
            source,
            batch_size=2,
            max_records=10,
            cancel_check=lambda: checks.append(1),
        )
    )

    assert [len(batch.records) for batch in batches] == [2, 2, 1]
    assert len(checks) == 3


def test_empty_json_array_produces_empty_inventory_warning(tmp_path: Path) -> None:
    source = tmp_path / "empty.json"
    source.write_text("[]", encoding="utf-8")
    inventory = inspect(JsonSourceAdapter(), source, SourceFormat.JSON)

    assert inventory.datasets[0].row_count == 0
    assert inventory.datasets[0].fields == ()
    assert inventory.warnings
