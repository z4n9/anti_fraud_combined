from __future__ import annotations

import csv
import json
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.risk_ledger.domain.contracts import (
    DatasetInventory,
    FieldInventory,
    PhysicalDataType,
    SourceFormat,
    SourceInventoryContract,
)
from app.risk_ledger.services.csv_reader import CsvFormatError, inspect_csv


DEFAULT_BATCH_SIZE = 5_000
DEFAULT_MAX_RECORDS = 1_000_000
DEFAULT_MAX_FIELDS = 1_000
DEFAULT_MAX_NESTING_DEPTH = 4
DISTINCT_TRACKING_LIMIT = 10_000
MAX_TEXT_RECORD_CHARS = 8 * 1024 * 1024

CancelCheck = Callable[[], None]


class SourceAdapterError(ValueError):
    """Raised when a supported source cannot be safely inventoried."""


class SourceRecordLimitError(SourceAdapterError):
    """Raised when a source exceeds the configured logical-record limit."""


@dataclass(frozen=True, slots=True)
class RecordBatch:
    dataset_id: str
    records: tuple[dict[str, Any], ...]
    field_paths: tuple[str, ...] = ()


class SourceAdapter(ABC):
    formats: tuple[SourceFormat, ...]

    @abstractmethod
    def iter_batches(
        self,
        path: str | Path,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_depth: int = DEFAULT_MAX_NESTING_DEPTH,
        cancel_check: CancelCheck | None = None,
    ) -> Iterator[RecordBatch]:
        """Yield bounded batches without loading the complete source into memory."""

    def inspect(
        self,
        path: str | Path,
        *,
        analysis_id: str,
        filename: str,
        source_format: SourceFormat,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_fields: int = DEFAULT_MAX_FIELDS,
        max_depth: int = DEFAULT_MAX_NESTING_DEPTH,
        cancel_check: CancelCheck | None = None,
    ) -> SourceInventoryContract:
        source_path = Path(path)
        profiler = _DatasetProfiler(max_fields=max_fields)
        for batch in self.iter_batches(
            source_path,
            batch_size=batch_size,
            max_records=max_records,
            max_depth=max_depth,
            cancel_check=cancel_check,
        ):
            profiler.consume(batch)
        return SourceInventoryContract(
            analysis_id=analysis_id,
            filename=filename,
            source_format=source_format,
            file_size_bytes=source_path.stat().st_size,
            datasets=profiler.build(),
            warnings=tuple(profiler.warnings),
        )


def _validated_limits(batch_size: int, max_records: int, max_depth: int) -> None:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if max_records <= 0:
        raise ValueError("max_records must be positive")
    if max_depth < 0:
        raise ValueError("max_depth cannot be negative")


def _yield_batches(
    records: Iterator[dict[str, Any]],
    *,
    dataset_id: str,
    batch_size: int,
    max_records: int,
    cancel_check: CancelCheck | None,
) -> Iterator[RecordBatch]:
    batch: list[dict[str, Any]] = []
    count = 0
    for record in records:
        count += 1
        if count > max_records:
            raise SourceRecordLimitError(
                f"Source contains more than {max_records:,} logical records."
            )
        batch.append(record)
        if len(batch) >= batch_size:
            if cancel_check is not None:
                cancel_check()
            yield RecordBatch(dataset_id=dataset_id, records=tuple(batch))
            batch.clear()
    if batch:
        if cancel_check is not None:
            cancel_check()
        yield RecordBatch(dataset_id=dataset_id, records=tuple(batch))


class CsvSourceAdapter(SourceAdapter):
    formats = (SourceFormat.CSV,)

    def iter_batches(
        self,
        path: str | Path,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_depth: int = DEFAULT_MAX_NESTING_DEPTH,
        cancel_check: CancelCheck | None = None,
    ) -> Iterator[RecordBatch]:
        _validated_limits(batch_size, max_records, max_depth)
        source_path = Path(path)
        metadata = inspect_csv(source_path)

        def records() -> Iterator[dict[str, Any]]:
            with source_path.open(
                "r", encoding=metadata.encoding, errors="strict", newline=""
            ) as stream:
                reader = csv.reader(stream, delimiter=metadata.delimiter)
                next(reader, None)
                width = len(metadata.columns)
                for line_number, values in enumerate(reader, start=2):
                    if len(values) != width:
                        raise SourceAdapterError(
                            "CSV row width differs from the header "
                            f"at logical line {line_number}."
                        )
                    yield dict(zip(metadata.columns, values, strict=True))

        yielded = False
        for batch in _yield_batches(
            records(),
            dataset_id="records",
            batch_size=batch_size,
            max_records=max_records,
            cancel_check=cancel_check,
        ):
            yielded = True
            yield RecordBatch(
                dataset_id=batch.dataset_id,
                records=batch.records,
                field_paths=tuple(metadata.columns),
            )
        if not yielded:
            yield RecordBatch(
                dataset_id="records",
                records=(),
                field_paths=tuple(metadata.columns),
            )


def _flatten_document(
    document: Mapping[str, Any],
    *,
    max_depth: int,
    prefix: str = "",
    depth: int = 0,
) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for raw_key, value in document.items():
        key = str(raw_key).strip()
        if not key:
            raise SourceAdapterError("JSON document contains an empty field name.")
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping) and depth < max_depth:
            nested = _flatten_document(
                value,
                max_depth=max_depth,
                prefix=path,
                depth=depth + 1,
            )
            for nested_path, nested_value in nested.items():
                if nested_path in flattened:
                    raise SourceAdapterError(
                        f"Flattened JSON field is duplicated: {nested_path}."
                    )
                flattened[nested_path] = nested_value
        else:
            if path in flattened:
                raise SourceAdapterError(f"JSON field is duplicated: {path}.")
            flattened[path] = value
    return flattened


def _iter_json_array(
    stream,
    *,
    chunk_size: int = 64 * 1024,
    max_record_chars: int | None = None,
) -> Iterator[Any]:
    if max_record_chars is None:
        max_record_chars = MAX_TEXT_RECORD_CHARS
    decoder = json.JSONDecoder()
    buffer = ""
    position = 0
    eof = False
    state = "start"

    while True:
        if not eof and (position >= len(buffer) or len(buffer) - position < chunk_size):
            buffer = buffer[position:]
            position = 0
            chunk = stream.read(chunk_size)
            if chunk:
                buffer += chunk
            else:
                eof = True

        if len(buffer) - position > max_record_chars:
            raise SourceAdapterError(
                "JSON record exceeds the safe per-record size limit."
            )

        while position < len(buffer) and buffer[position].isspace():
            position += 1

        if state == "start":
            if position >= len(buffer):
                if eof:
                    raise SourceAdapterError("JSON source is empty.")
                continue
            if buffer[position] == "[":
                position += 1
                state = "value_or_end"
                continue
            try:
                value, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError as error:
                if not eof:
                    continue
                raise SourceAdapterError(f"Invalid JSON document: {error.msg}.") from error
            position = end
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position != len(buffer):
                raise SourceAdapterError("JSON contains data after the root document.")
            while not eof:
                trailing = stream.read(chunk_size)
                if not trailing:
                    eof = True
                elif trailing.strip():
                    raise SourceAdapterError(
                        "JSON contains data after the root document."
                    )
            yield value
            return

        if state == "value_or_end":
            if position >= len(buffer):
                if eof:
                    raise SourceAdapterError("JSON array is not closed.")
                continue
            if buffer[position] == "]":
                position += 1
                state = "done"
                continue
            try:
                value, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError as error:
                if not eof:
                    continue
                raise SourceAdapterError(f"Invalid JSON array: {error.msg}.") from error
            position = end
            yield value
            state = "separator"
            continue

        if state == "separator":
            if position >= len(buffer):
                if eof:
                    raise SourceAdapterError("JSON array is not closed.")
                continue
            if buffer[position] == ",":
                position += 1
                state = "value_or_end"
                continue
            if buffer[position] == "]":
                position += 1
                state = "done"
                continue
            raise SourceAdapterError("JSON array items must be separated by a comma.")

        if state == "done":
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position != len(buffer):
                raise SourceAdapterError("JSON contains data after the root array.")
            while not eof:
                trailing = stream.read(chunk_size)
                if not trailing:
                    eof = True
                elif trailing.strip():
                    raise SourceAdapterError(
                        "JSON contains data after the root array."
                    )
            return


def _as_document(value: Any, *, logical_record: int, max_depth: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceAdapterError(
            f"Logical record {logical_record} must be a JSON object."
        )
    return _flatten_document(value, max_depth=max_depth)


class JsonSourceAdapter(SourceAdapter):
    formats = (SourceFormat.JSON,)

    def iter_batches(
        self,
        path: str | Path,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_depth: int = DEFAULT_MAX_NESTING_DEPTH,
        cancel_check: CancelCheck | None = None,
    ) -> Iterator[RecordBatch]:
        _validated_limits(batch_size, max_records, max_depth)
        source_path = Path(path)

        def records() -> Iterator[dict[str, Any]]:
            try:
                with source_path.open("r", encoding="utf-8-sig", errors="strict") as stream:
                    for number, value in enumerate(_iter_json_array(stream), start=1):
                        yield _as_document(
                            value,
                            logical_record=number,
                            max_depth=max_depth,
                        )
            except UnicodeError as error:
                raise SourceAdapterError("JSON must use UTF-8 encoding.") from error

        yield from _yield_batches(
            records(),
            dataset_id="records",
            batch_size=batch_size,
            max_records=max_records,
            cancel_check=cancel_check,
        )


class JsonLinesSourceAdapter(SourceAdapter):
    formats = (SourceFormat.JSONL, SourceFormat.NDJSON)

    def iter_batches(
        self,
        path: str | Path,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_depth: int = DEFAULT_MAX_NESTING_DEPTH,
        cancel_check: CancelCheck | None = None,
    ) -> Iterator[RecordBatch]:
        _validated_limits(batch_size, max_records, max_depth)
        source_path = Path(path)

        def records() -> Iterator[dict[str, Any]]:
            try:
                with source_path.open("r", encoding="utf-8-sig", errors="strict") as stream:
                    logical_record = 0
                    line_number = 0
                    while True:
                        line = stream.readline(MAX_TEXT_RECORD_CHARS + 1)
                        if not line:
                            break
                        line_number += 1
                        if len(line) > MAX_TEXT_RECORD_CHARS:
                            raise SourceAdapterError(
                                f"JSON record at line {line_number} exceeds the safe size limit."
                            )
                        if not line.strip():
                            continue
                        logical_record += 1
                        try:
                            value = json.loads(line)
                        except json.JSONDecodeError as error:
                            raise SourceAdapterError(
                                f"Invalid JSON at line {line_number}: {error.msg}."
                            ) from error
                        yield _as_document(
                            value,
                            logical_record=logical_record,
                            max_depth=max_depth,
                        )
            except UnicodeError as error:
                raise SourceAdapterError("JSON lines must use UTF-8 encoding.") from error

        yield from _yield_batches(
            records(),
            dataset_id="records",
            batch_size=batch_size,
            max_records=max_records,
            cancel_check=cancel_check,
        )


class SourceAdapterRegistry:
    def __init__(self, adapters: Sequence[SourceAdapter] | None = None) -> None:
        if adapters is None:
            from app.risk_ledger.services.database_adapters import (
                BsonSourceAdapter,
                SqlDumpSourceAdapter,
                SqliteSourceAdapter,
            )

            selected = (
                CsvSourceAdapter(),
                JsonSourceAdapter(),
                JsonLinesSourceAdapter(),
                SqliteSourceAdapter(),
                SqlDumpSourceAdapter(),
                BsonSourceAdapter(),
            )
        else:
            selected = adapters
        self._adapters: dict[SourceFormat, SourceAdapter] = {}
        for adapter in selected:
            for source_format in adapter.formats:
                if source_format in self._adapters:
                    raise ValueError(f"Duplicate source adapter: {source_format.value}")
                self._adapters[source_format] = adapter

    def get(self, source_format: SourceFormat) -> SourceAdapter:
        try:
            return self._adapters[source_format]
        except KeyError as error:
            raise SourceAdapterError(
                f"Source adapter is not implemented for {source_format.value}."
            ) from error

    def supports(self, source_format: SourceFormat) -> bool:
        return source_format in self._adapters


@dataclass(slots=True)
class _FieldStats:
    source_path: str
    present: int = 0
    non_missing: int = 0
    types: set[PhysicalDataType] = field(default_factory=set)
    distinct: set[str] | None = field(default_factory=set)
    time_min: datetime | None = None
    time_max: datetime | None = None

    def add(self, value: Any) -> None:
        self.present += 1
        value_type = _physical_type(value)
        if value_type == PhysicalDataType.NULL:
            return
        self.non_missing += 1
        self.types.add(value_type)
        temporal = _temporal_value(value, value_type)
        if temporal is not None:
            self.time_min = temporal if self.time_min is None else min(self.time_min, temporal)
            self.time_max = temporal if self.time_max is None else max(self.time_max, temporal)
        if self.distinct is not None:
            self.distinct.add(_distinct_token(value))
            if len(self.distinct) > DISTINCT_TRACKING_LIMIT:
                self.distinct = None

    def physical_type(self) -> PhysicalDataType:
        if not self.types:
            return PhysicalDataType.NULL
        if self.types <= {PhysicalDataType.INTEGER, PhysicalDataType.NUMBER}:
            return (
                PhysicalDataType.NUMBER
                if PhysicalDataType.NUMBER in self.types
                else PhysicalDataType.INTEGER
            )
        if self.types <= {PhysicalDataType.DATE, PhysicalDataType.DATETIME}:
            return (
                PhysicalDataType.DATETIME
                if PhysicalDataType.DATETIME in self.types
                else PhysicalDataType.DATE
            )
        if len(self.types) == 1:
            return next(iter(self.types))
        return PhysicalDataType.MIXED


@dataclass(slots=True)
class _DatasetStats:
    dataset_id: str
    row_count: int = 0
    fields: dict[str, _FieldStats] = field(default_factory=dict)


class _DatasetProfiler:
    def __init__(self, *, max_fields: int) -> None:
        self.max_fields = max_fields
        self.datasets: dict[str, _DatasetStats] = {}
        self.warnings: list[str] = []

    def consume(self, batch: RecordBatch) -> None:
        dataset = self.datasets.setdefault(
            batch.dataset_id,
            _DatasetStats(dataset_id=batch.dataset_id),
        )
        for path in batch.field_paths:
            if path not in dataset.fields:
                if len(dataset.fields) >= self.max_fields:
                    raise SourceAdapterError(
                        f"Dataset contains more than {self.max_fields:,} fields."
                    )
                dataset.fields[path] = _FieldStats(source_path=path)
        for record in batch.records:
            for path in record:
                if path not in dataset.fields:
                    if len(dataset.fields) >= self.max_fields:
                        raise SourceAdapterError(
                            f"Dataset contains more than {self.max_fields:,} fields."
                        )
                    dataset.fields[path] = _FieldStats(source_path=path)
            dataset.row_count += 1
            for path, stats in dataset.fields.items():
                if path in record:
                    stats.add(record[path])

    def build(self) -> tuple[DatasetInventory, ...]:
        inventories: list[DatasetInventory] = []
        for dataset in self.datasets.values():
            fields: list[FieldInventory] = []
            primary_keys: list[str] = []
            time_fields: list[str] = []
            for path, stats in dataset.fields.items():
                physical_type = stats.physical_type()
                missing_count = dataset.row_count - stats.non_missing
                distinct_count = (
                    len(stats.distinct) if stats.distinct is not None else None
                )
                likely_identifier = _likely_identifier(
                    path,
                    distinct_count=distinct_count,
                    non_missing=stats.non_missing,
                    row_count=dataset.row_count,
                )
                if likely_identifier and missing_count == 0:
                    primary_keys.append(path)
                if _likely_time_field(path, physical_type):
                    time_fields.append(path)
                fields.append(
                    FieldInventory(
                        source_path=path,
                        display_label=_display_label(path),
                        physical_type=physical_type,
                        nullable=missing_count > 0,
                        missing_rate=(
                            missing_count / dataset.row_count
                            if dataset.row_count
                            else 0
                        ),
                        distinct_count=distinct_count,
                        likely_identifier=likely_identifier,
                        likely_sensitive=_likely_sensitive(path),
                        time_min=stats.time_min,
                        time_max=stats.time_max,
                    )
                )
            inventories.append(
                DatasetInventory(
                    dataset_id=dataset.dataset_id,
                    display_label="Данные источника",
                    row_count=dataset.row_count,
                    fields=tuple(fields),
                    primary_key_candidates=tuple(primary_keys),
                    time_field_candidates=tuple(time_fields),
                )
            )
        if not inventories:
            inventories.append(
                DatasetInventory(
                    dataset_id="records",
                    display_label="Данные источника",
                    row_count=0,
                    fields=(),
                )
            )
            self.warnings.append("Источник не содержит логических записей.")
        return tuple(inventories)


_INTEGER = re.compile(r"^[+-]?\d+$")
_NUMBER = re.compile(
    r"^[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?$"
)
_IDENTIFIER = re.compile(
    r"(^|[._])(id|identifier|record_id|client_id|account_id|transaction_id|uuid)$",
    re.IGNORECASE,
)
_TIME_NAME = re.compile(r"(^|[._])(date|time|timestamp|created_at|updated_at|datetime)$", re.I)
_SENSITIVE = re.compile(
    r"(^|[._])(name|fio|phone|email|passport|document|card|account|iban|address|birth)",
    re.IGNORECASE,
)


def _physical_type(value: Any) -> PhysicalDataType:
    if value is None or (isinstance(value, str) and not value.strip()):
        return PhysicalDataType.NULL
    if isinstance(value, bool):
        return PhysicalDataType.BOOLEAN
    if isinstance(value, int):
        return PhysicalDataType.INTEGER
    if isinstance(value, float):
        return PhysicalDataType.NUMBER
    if isinstance(value, datetime):
        return PhysicalDataType.DATETIME
    if isinstance(value, date):
        return PhysicalDataType.DATE
    if isinstance(value, Mapping):
        return PhysicalDataType.OBJECT
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return PhysicalDataType.ARRAY
    text = str(value).strip()
    lowered = text.casefold()
    if lowered in {"true", "false"}:
        return PhysicalDataType.BOOLEAN
    if _INTEGER.fullmatch(text):
        return PhysicalDataType.INTEGER
    if _NUMBER.fullmatch(text):
        try:
            if math.isfinite(float(text.replace(",", "."))):
                return PhysicalDataType.NUMBER
        except ValueError:
            pass
    iso_value = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso_value)
        return (
            PhysicalDataType.DATETIME
            if "T" in text or " " in text or parsed.time().isoformat() != "00:00:00"
            else PhysicalDataType.DATE
        )
    except ValueError:
        return PhysicalDataType.STRING


def _temporal_value(
    value: Any,
    value_type: PhysicalDataType,
) -> datetime | None:
    if value_type not in {PhysicalDataType.DATE, PhysicalDataType.DATETIME}:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        text = str(value).strip()
        iso_value = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(iso_value)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _distinct_token(value: Any) -> str:
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _likely_identifier(
    path: str,
    *,
    distinct_count: int | None,
    non_missing: int,
    row_count: int,
) -> bool:
    named_identifier = _IDENTIFIER.search(path) is not None
    unique = distinct_count is not None and distinct_count == non_missing == row_count
    return named_identifier and unique


def _likely_time_field(path: str, physical_type: PhysicalDataType) -> bool:
    return physical_type in {PhysicalDataType.DATE, PhysicalDataType.DATETIME} or (
        _TIME_NAME.search(path) is not None
    )


def _likely_sensitive(path: str) -> bool:
    return _SENSITIVE.search(path) is not None


def _display_label(path: str) -> str:
    label = path.rsplit(".", 1)[-1].replace("_", " ").replace("-", " ").strip()
    return label[:1].upper() + label[1:] if label else path
