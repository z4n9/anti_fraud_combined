from __future__ import annotations

import re
import sqlite3
import struct
from collections.abc import Iterator, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.risk_ledger.domain.contracts import (
    DatasetInventory,
    IndexInventory,
    RelationshipCandidate,
    RelationshipKind,
    SourceFormat,
    SourceInventoryContract,
)
from app.risk_ledger.services.source_adapters import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_FIELDS,
    DEFAULT_MAX_NESTING_DEPTH,
    DEFAULT_MAX_RECORDS,
    CancelCheck,
    RecordBatch,
    SourceAdapter,
    SourceAdapterError,
    SourceRecordLimitError,
    _DatasetProfiler,
    _flatten_document,
    _validated_limits,
)


MAX_SQL_STATEMENT_CHARS = 8 * 1024 * 1024
MAX_BSON_DOCUMENT_BYTES = 16 * 1024 * 1024


def _check_record_limit(count: int, max_records: int) -> None:
    if count > max_records:
        raise SourceRecordLimitError(
            f"Source contains more than {max_records:,} logical records."
        )


def _quote_sqlite_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


@dataclass(frozen=True, slots=True)
class _SqliteDataset:
    name: str
    kind: str
    columns: tuple[str, ...]
    primary_keys: tuple[str, ...]
    indexes: tuple[IndexInventory, ...]


@dataclass(frozen=True, slots=True)
class _SqliteSchema:
    datasets: tuple[_SqliteDataset, ...]
    relationships: tuple[RelationshipCandidate, ...]


class SqliteSourceAdapter(SourceAdapter):
    formats = (SourceFormat.SQLITE,)

    @staticmethod
    def _connect(path: str | Path) -> sqlite3.Connection:
        source_path = Path(path).resolve()
        uri = source_path.as_uri() + "?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only = ON")
        try:
            connection.enable_load_extension(False)
        except AttributeError:
            pass
        return connection

    def _schema(self, path: str | Path) -> _SqliteSchema:
        datasets: list[_SqliteDataset] = []
        relationships: list[RelationshipCandidate] = []
        try:
            with closing(self._connect(path)) as connection:
                objects = connection.execute(
                    "SELECT name, type FROM sqlite_schema "
                    "WHERE type IN ('table', 'view') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                for raw_name, raw_kind in objects:
                    name = str(raw_name)
                    quoted = _quote_sqlite_identifier(name)
                    columns_info = connection.execute(
                        f"PRAGMA table_info({quoted})"
                    ).fetchall()
                    columns = tuple(str(row[1]) for row in columns_info)
                    primary_keys = tuple(
                        str(row[1])
                        for row in sorted(columns_info, key=lambda row: int(row[5]))
                        if int(row[5]) > 0
                    )
                    indexes: list[IndexInventory] = []
                    if raw_kind == "table":
                        for index_row in connection.execute(
                            f"PRAGMA index_list({quoted})"
                        ).fetchall():
                            index_name = str(index_row[1])
                            quoted_index = _quote_sqlite_identifier(index_name)
                            index_fields = tuple(
                                str(field_row[2])
                                for field_row in connection.execute(
                                    f"PRAGMA index_info({quoted_index})"
                                ).fetchall()
                                if field_row[2] is not None
                            )
                            indexes.append(
                                IndexInventory(
                                    name=index_name,
                                    fields=index_fields,
                                    unique=bool(index_row[2]),
                                )
                            )
                    datasets.append(
                        _SqliteDataset(
                            name=name,
                            kind=str(raw_kind),
                            columns=columns,
                            primary_keys=primary_keys,
                            indexes=tuple(indexes),
                        )
                    )
                    if raw_kind == "table":
                        for foreign_key in connection.execute(
                            f"PRAGMA foreign_key_list({quoted})"
                        ).fetchall():
                            relationships.append(
                                RelationshipCandidate(
                                    from_dataset=name,
                                    from_field=str(foreign_key[3]),
                                    to_dataset=str(foreign_key[2]),
                                    to_field=str(foreign_key[4]),
                                    kind=RelationshipKind.FOREIGN_KEY,
                                    confidence=1,
                                )
                            )
        except sqlite3.DatabaseError as error:
            raise SourceAdapterError("SQLite file cannot be safely read.") from error
        return _SqliteSchema(tuple(datasets), tuple(relationships))

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
        schema = self._schema(path)
        count = 0
        try:
            with closing(self._connect(path)) as connection:
                connection.row_factory = sqlite3.Row
                for dataset in schema.datasets:
                    if cancel_check is not None:
                        cancel_check()
                    quoted_dataset = _quote_sqlite_identifier(dataset.name)
                    cursor = connection.execute(f"SELECT * FROM {quoted_dataset}")
                    yielded = False
                    while rows := cursor.fetchmany(batch_size):
                        yielded = True
                        count += len(rows)
                        _check_record_limit(count, max_records)
                        if cancel_check is not None:
                            cancel_check()
                        yield RecordBatch(
                            dataset_id=dataset.name,
                            records=tuple(dict(row) for row in rows),
                            field_paths=dataset.columns,
                        )
                    if not yielded:
                        yield RecordBatch(
                            dataset_id=dataset.name,
                            records=(),
                            field_paths=dataset.columns,
                        )
        except sqlite3.DatabaseError as error:
            raise SourceAdapterError("SQLite dataset cannot be safely read.") from error

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
        schema = self._schema(path)
        profiler = _DatasetProfiler(max_fields=max_fields)
        for batch in self.iter_batches(
            path,
            batch_size=batch_size,
            max_records=max_records,
            max_depth=max_depth,
            cancel_check=cancel_check,
        ):
            profiler.consume(batch)
        inventory_by_id = {item.dataset_id: item for item in profiler.build()}
        datasets: list[DatasetInventory] = []
        for definition in schema.datasets:
            item = inventory_by_id[definition.name]
            datasets.append(
                item.model_copy(
                    update={
                        "display_label": definition.name,
                        "primary_key_candidates": definition.primary_keys,
                        "indexes": definition.indexes,
                    }
                )
            )
        warnings: list[str] = []
        if not datasets:
            warnings.append("SQLite не содержит пользовательских таблиц или представлений.")
        return SourceInventoryContract(
            analysis_id=analysis_id,
            filename=filename,
            source_format=source_format,
            file_size_bytes=Path(path).stat().st_size,
            datasets=tuple(datasets),
            relationships=schema.relationships,
            warnings=tuple(warnings),
        )


_DANGEROUS_SQL = re.compile(
    r"^\s*(?:CREATE\s+(?:FUNCTION|PROCEDURE|TRIGGER)|DO\b|EXEC(?:UTE)?\b|"
    r"ATTACH\b|DETACH\b|LOAD\s+DATA\b|INSTALL\b|CALL\b|GRANT\b|REVOKE\b)",
    re.IGNORECASE | re.DOTALL,
)
_DANGEROUS_ANYWHERE = re.compile(
    r"\bxp_cmdshell\b|\bCOPY\b[\s\S]*\bPROGRAM\b|\bINTO\s+OUTFILE\b",
    re.IGNORECASE,
)
_IGNORED_SQL = re.compile(
    r"^\s*(?:SET\b|BEGIN\b|COMMIT\b|ROLLBACK\b|START\s+TRANSACTION\b|"
    r"USE\b|LOCK\b|UNLOCK\b|ALTER\s+TABLE\b|CREATE\s+(?:UNIQUE\s+)?INDEX\b|"
    r"DROP\s+(?:TABLE|VIEW|INDEX)\b|PRAGMA\b)",
    re.IGNORECASE | re.DOTALL,
)


def _detect_text_encoding(path: Path) -> str:
    sample = path.read_bytes()[: 64 * 1024]
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise SourceAdapterError("SQL dump must use UTF-8 or Windows-1251 encoding.")


def _strip_sql_comments(statement: str) -> str:
    output: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(statement):
        current = statement[index]
        following = statement[index + 1] if index + 1 < len(statement) else ""
        if quote:
            output.append(current)
            if current == quote:
                if following == quote:
                    output.append(following)
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if current in {"'", '"', "`"}:
            quote = current
            output.append(current)
            index += 1
            continue
        if current == "-" and following == "-":
            newline = statement.find("\n", index + 2)
            index = len(statement) if newline < 0 else newline + 1
            output.append("\n")
            continue
        if current == "/" and following == "*":
            end = statement.find("*/", index + 2)
            if end < 0:
                raise SourceAdapterError("SQL dump contains an unclosed block comment.")
            index = end + 2
            continue
        output.append(current)
        index += 1
    return "".join(output).strip()


def _extract_sql_statements(buffer: str) -> tuple[list[str], str]:
    statements: list[str] = []
    start = 0
    quote: str | None = None
    index = 0
    while index < len(buffer):
        current = buffer[index]
        following = buffer[index + 1] if index + 1 < len(buffer) else ""
        if quote:
            if current == quote:
                if following == quote:
                    index += 2
                    continue
                quote = None
            elif current == "\\" and quote == "'":
                index += 2
                continue
        elif current in {"'", '"', "`"}:
            quote = current
        elif current == ";":
            statements.append(buffer[start:index])
            start = index + 1
        index += 1
    return statements, buffer[start:]


def _split_top_level(value: str, delimiter: str = ",") -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(value):
        current = value[index]
        following = value[index + 1] if index + 1 < len(value) else ""
        if quote:
            if current == quote:
                if following == quote:
                    index += 2
                    continue
                quote = None
            elif current == "\\" and quote == "'":
                index += 2
                continue
        elif current in {"'", '"', "`"}:
            quote = current
        elif current == "(":
            depth += 1
        elif current == ")":
            depth -= 1
            if depth < 0:
                raise SourceAdapterError("SQL statement has unbalanced parentheses.")
        elif current == delimiter and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
        index += 1
    if quote or depth != 0:
        raise SourceAdapterError("SQL statement has an unclosed value or parentheses.")
    parts.append(value[start:].strip())
    return parts


def _normalize_identifier(value: str) -> str:
    value = value.strip().rstrip(";")
    parts = []
    for part in _split_identifier_parts(value):
        part = part.strip()
        if (part.startswith('"') and part.endswith('"')) or (
            part.startswith("`") and part.endswith("`")
        ):
            part = part[1:-1]
        elif part.startswith("[") and part.endswith("]"):
            part = part[1:-1]
        parts.append(part)
    normalized = ".".join(parts)
    if not normalized or any(ord(character) < 32 for character in normalized):
        raise SourceAdapterError("SQL contains an invalid identifier.")
    return normalized


def _split_identifier_parts(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    quote: str | None = None
    closing = {"[": "]", '"': '"', "`": "`"}
    for index, character in enumerate(value):
        if quote:
            if character == quote:
                quote = None
        elif character in closing:
            quote = closing[character]
        elif character == ".":
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return parts


def _leading_identifier(value: str) -> tuple[str, str]:
    value = value.lstrip()
    if not value:
        raise SourceAdapterError("SQL identifier is missing.")
    index = 0
    quote: str | None = None
    closing = {"[": "]", '"': '"', "`": "`"}
    while index < len(value):
        character = value[index]
        if quote:
            if character == quote:
                quote = None
        elif character in closing:
            quote = closing[character]
        elif character.isspace() or character == "(":
            break
        index += 1
    return _normalize_identifier(value[:index]), value[index:]


def _parse_create_table(statement: str) -> tuple[str, tuple[str, ...]]:
    match = re.match(
        r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(.+)$",
        statement,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise SourceAdapterError("Unsupported CREATE TABLE statement.")
    table, remainder = _leading_identifier(match.group(1))
    opening = remainder.find("(")
    closing = remainder.rfind(")")
    if opening < 0 or closing <= opening:
        raise SourceAdapterError("CREATE TABLE does not contain a valid column list.")
    definitions = _split_top_level(remainder[opening + 1 : closing])
    columns: list[str] = []
    for definition in definitions:
        if re.match(
            r"^(?:CONSTRAINT\b|PRIMARY\s+KEY\b|FOREIGN\s+KEY\b|UNIQUE\b|CHECK\b)",
            definition,
            re.IGNORECASE,
        ):
            continue
        column, _ = _leading_identifier(definition)
        columns.append(column)
    if not columns:
        raise SourceAdapterError("CREATE TABLE does not define data columns.")
    return table, tuple(columns)


def _parse_sql_literal(token: str) -> Any:
    value = token.strip()
    if re.fullmatch(r"NULL", value, re.IGNORECASE):
        return None
    if re.fullmatch(r"TRUE|FALSE", value, re.IGNORECASE):
        return value.casefold() == "true"
    if re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?", value):
        return float(value)
    string_value = value[1:] if value[:1].casefold() == "n" else value
    if len(string_value) >= 2 and string_value[0] == string_value[-1] == "'":
        return string_value[1:-1].replace("''", "'").replace("\\'", "'")
    if re.fullmatch(r"(?:0x|X')[0-9a-fA-F]+'?", value):
        return value
    if re.match(r"^(?:DEFAULT|CURRENT_TIMESTAMP|now\(\))$", value, re.I):
        return value
    raise SourceAdapterError("SQL dump contains an unsupported literal expression.")


def _tuple_groups(value: str) -> Iterator[str]:
    index = 0
    while index < len(value):
        while index < len(value) and (value[index].isspace() or value[index] == ","):
            index += 1
        if index >= len(value):
            return
        if value[index] != "(":
            raise SourceAdapterError("INSERT VALUES must contain row tuples.")
        start = index + 1
        depth = 1
        quote: str | None = None
        index += 1
        while index < len(value) and depth:
            current = value[index]
            following = value[index + 1] if index + 1 < len(value) else ""
            if quote:
                if current == quote:
                    if following == quote:
                        index += 2
                        continue
                    quote = None
                elif current == "\\":
                    index += 2
                    continue
            elif current == "'":
                quote = current
            elif current == "(":
                depth += 1
            elif current == ")":
                depth -= 1
                if depth == 0:
                    yield value[start:index]
                    index += 1
                    break
            index += 1
        if depth:
            raise SourceAdapterError("INSERT VALUES contains an unclosed row tuple.")


def _parse_insert(
    statement: str,
    schemas: Mapping[str, tuple[str, ...]],
) -> tuple[str, tuple[str, ...], Iterator[dict[str, Any]]]:
    match = re.match(
        r"^\s*INSERT\s+(?:IGNORE\s+)?INTO\s+(.+)$",
        statement,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise SourceAdapterError("Unsupported INSERT statement.")
    table, remainder = _leading_identifier(match.group(1))
    remainder = remainder.lstrip()
    columns: tuple[str, ...]
    if remainder.startswith("("):
        depth = 0
        closing = -1
        for index, character in enumerate(remainder):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing < 0:
            raise SourceAdapterError("INSERT column list is not closed.")
        columns = tuple(
            _normalize_identifier(item)
            for item in _split_top_level(remainder[1:closing])
        )
        remainder = remainder[closing + 1 :].lstrip()
    else:
        columns = schemas.get(table, ())
    values_match = re.match(r"^VALUES\s+(.+)$", remainder, re.I | re.S)
    if not values_match:
        raise SourceAdapterError("Only INSERT ... VALUES is supported.")
    if not columns:
        raise SourceAdapterError(
            f"INSERT for {table} requires a column list or preceding CREATE TABLE."
        )

    def rows() -> Iterator[dict[str, Any]]:
        for group in _tuple_groups(values_match.group(1)):
            values = tuple(_parse_sql_literal(item) for item in _split_top_level(group))
            if len(values) != len(columns):
                raise SourceAdapterError(
                    f"INSERT row width differs from columns for {table}."
                )
            yield dict(zip(columns, values, strict=True))

    return table, columns, rows()


def _parse_copy_header(statement: str) -> tuple[str, tuple[str, ...]] | None:
    match = re.match(
        r"^\s*COPY\s+(.+?)\s*(?:\((.*?)\))?\s+FROM\s+STDIN\s*$",
        statement,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    table = _normalize_identifier(match.group(1))
    columns = tuple(
        _normalize_identifier(item)
        for item in _split_top_level(match.group(2) or "")
        if item
    )
    return table, columns


class SqlDumpSourceAdapter(SourceAdapter):
    formats = (SourceFormat.SQL_DUMP,)

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
        encoding = _detect_text_encoding(source_path)
        schemas: dict[str, tuple[str, ...]] = {}
        batches: dict[str, list[dict[str, Any]]] = {}
        counts_by_table: dict[str, int] = {}
        record_count = 0

        def append(table: str, columns: tuple[str, ...], record: dict[str, Any]):
            nonlocal record_count
            record_count += 1
            counts_by_table[table] = counts_by_table.get(table, 0) + 1
            _check_record_limit(record_count, max_records)
            batch = batches.setdefault(table, [])
            batch.append(record)
            if len(batch) >= batch_size:
                if cancel_check is not None:
                    cancel_check()
                payload = RecordBatch(table, tuple(batch), columns)
                batch.clear()
                return payload
            return None

        buffer = ""
        copy_target: tuple[str, tuple[str, ...]] | None = None
        try:
            with source_path.open("r", encoding=encoding, errors="strict") as stream:
                for raw_line in stream:
                    if copy_target is not None:
                        if raw_line.rstrip("\r\n") == r"\.":
                            copy_target = None
                            continue
                        table, columns = copy_target
                        values = raw_line.rstrip("\r\n").split("\t")
                        if len(values) != len(columns):
                            raise SourceAdapterError(
                                f"COPY row width differs from columns for {table}."
                            )
                        record = {
                            column: None if value == r"\N" else value
                            for column, value in zip(columns, values, strict=True)
                        }
                        if payload := append(table, columns, record):
                            yield payload
                        continue

                    if raw_line.strip().upper() == "GO":
                        raw_line = ";\n"
                    buffer += raw_line
                    if len(buffer) > MAX_SQL_STATEMENT_CHARS:
                        raise SourceAdapterError("SQL statement exceeds the safe size limit.")
                    statements, buffer = _extract_sql_statements(buffer)
                    for raw_statement in statements:
                        statement = _strip_sql_comments(raw_statement)
                        if not statement:
                            continue
                        if _DANGEROUS_SQL.search(statement) or _DANGEROUS_ANYWHERE.search(
                            statement
                        ):
                            raise SourceAdapterError(
                                "SQL dump contains a prohibited executable statement."
                            )
                        if copy := _parse_copy_header(statement):
                            table, columns = copy
                            columns = columns or schemas.get(table, ())
                            if not columns:
                                raise SourceAdapterError(
                                    f"COPY for {table} has no known columns."
                                )
                            copy_target = (table, columns)
                        elif re.match(r"^\s*CREATE\s+TABLE\b", statement, re.I):
                            table, columns = _parse_create_table(statement)
                            schemas[table] = columns
                            batches.setdefault(table, [])
                        elif re.match(r"^\s*INSERT\b", statement, re.I):
                            table, columns, rows = _parse_insert(statement, schemas)
                            schemas.setdefault(table, columns)
                            for record in rows:
                                if payload := append(table, columns, record):
                                    yield payload
                        elif _IGNORED_SQL.search(statement):
                            continue
                        else:
                            raise SourceAdapterError(
                                "SQL dump contains an unsupported statement."
                            )
        except UnicodeError as error:
            raise SourceAdapterError("SQL dump has an invalid text encoding.") from error

        trailing = _strip_sql_comments(buffer)
        if trailing:
            raise SourceAdapterError("SQL dump ends with an incomplete statement.")
        if copy_target is not None:
            raise SourceAdapterError("COPY data is not terminated with \\..")
        for table, columns in schemas.items():
            batch = batches.get(table, [])
            if batch:
                if cancel_check is not None:
                    cancel_check()
                yield RecordBatch(table, tuple(batch), columns)
            elif counts_by_table.get(table, 0) == 0:
                yield RecordBatch(table, (), columns)


class _BsonReader:
    def __init__(self, payload: bytes, *, max_depth: int) -> None:
        self.payload = payload
        self.max_depth = max_depth

    def _require(self, offset: int, size: int, end: int) -> None:
        if size < 0 or offset < 0 or offset + size > end:
            raise SourceAdapterError("BSON document is truncated.")

    def _cstring(self, offset: int, end: int) -> tuple[str, int]:
        terminator = self.payload.find(b"\x00", offset, end)
        if terminator < 0:
            raise SourceAdapterError("BSON cstring is not terminated.")
        try:
            value = self.payload[offset:terminator].decode("utf-8")
        except UnicodeDecodeError as error:
            raise SourceAdapterError("BSON field name is not valid UTF-8.") from error
        return value, terminator + 1

    def document(self, offset: int = 0, *, depth: int = 0) -> tuple[dict[str, Any], int]:
        if depth > self.max_depth:
            raise SourceAdapterError("BSON nesting exceeds the safe depth limit.")
        self._require(offset, 4, len(self.payload))
        length = struct.unpack_from("<i", self.payload, offset)[0]
        if length < 5 or length > MAX_BSON_DOCUMENT_BYTES:
            raise SourceAdapterError("BSON document size is outside the safe limit.")
        end = offset + length
        self._require(offset, length, len(self.payload))
        if self.payload[end - 1] != 0:
            raise SourceAdapterError("BSON document terminator is missing.")
        values: dict[str, Any] = {}
        cursor = offset + 4
        while cursor < end - 1:
            element_type = self.payload[cursor]
            cursor += 1
            key, cursor = self._cstring(cursor, end)
            if element_type in {0x0D, 0x0F}:
                raise SourceAdapterError("BSON JavaScript values are prohibited.")
            value, cursor = self._value(element_type, cursor, end, depth)
            values[key] = value
        if cursor != end - 1:
            raise SourceAdapterError("BSON document has invalid element boundaries.")
        return values, end

    def _value(
        self,
        element_type: int,
        cursor: int,
        end: int,
        depth: int,
    ) -> tuple[Any, int]:
        if element_type == 0x01:
            self._require(cursor, 8, end)
            return struct.unpack_from("<d", self.payload, cursor)[0], cursor + 8
        if element_type in {0x02, 0x0D}:
            self._require(cursor, 4, end)
            length = struct.unpack_from("<i", self.payload, cursor)[0]
            self._require(cursor + 4, length, end)
            if length < 1 or self.payload[cursor + 3 + length] != 0:
                raise SourceAdapterError("BSON string is invalid.")
            try:
                value = self.payload[cursor + 4 : cursor + 3 + length].decode("utf-8")
            except UnicodeDecodeError as error:
                raise SourceAdapterError("BSON string is not valid UTF-8.") from error
            return value, cursor + 4 + length
        if element_type in {0x03, 0x04}:
            value, next_cursor = self.document(cursor, depth=depth + 1)
            if element_type == 0x04:
                try:
                    ordered = [value[str(index)] for index in range(len(value))]
                except KeyError as error:
                    raise SourceAdapterError("BSON array indexes are invalid.") from error
                return ordered, next_cursor
            return value, next_cursor
        if element_type == 0x05:
            self._require(cursor, 5, end)
            length = struct.unpack_from("<i", self.payload, cursor)[0]
            self._require(cursor + 5, length, end)
            return self.payload[cursor + 5 : cursor + 5 + length].hex(), cursor + 5 + length
        if element_type == 0x07:
            self._require(cursor, 12, end)
            return self.payload[cursor : cursor + 12].hex(), cursor + 12
        if element_type == 0x08:
            self._require(cursor, 1, end)
            boolean = self.payload[cursor]
            if boolean not in {0, 1}:
                raise SourceAdapterError("BSON boolean value is invalid.")
            return bool(boolean), cursor + 1
        if element_type == 0x09:
            self._require(cursor, 8, end)
            milliseconds = struct.unpack_from("<q", self.payload, cursor)[0]
            return datetime.fromtimestamp(milliseconds / 1000, tz=UTC), cursor + 8
        if element_type in {0x06, 0x0A}:
            return None, cursor
        if element_type == 0x10:
            self._require(cursor, 4, end)
            return struct.unpack_from("<i", self.payload, cursor)[0], cursor + 4
        if element_type in {0x11, 0x12}:
            self._require(cursor, 8, end)
            return struct.unpack_from("<q", self.payload, cursor)[0], cursor + 8
        if element_type == 0x13:
            self._require(cursor, 16, end)
            return self.payload[cursor : cursor + 16].hex(), cursor + 16
        if element_type == 0x0B:
            pattern, cursor = self._cstring(cursor, end)
            options, cursor = self._cstring(cursor, end)
            return f"/{pattern}/{options}", cursor
        if element_type in {0x7F, 0xFF}:
            return "max_key" if element_type == 0x7F else "min_key", cursor
        raise SourceAdapterError(
            f"BSON contains unsupported element type 0x{element_type:02x}."
        )


class BsonSourceAdapter(SourceAdapter):
    formats = (SourceFormat.BSON,)

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
        records: list[dict[str, Any]] = []
        count = 0
        with source_path.open("rb") as stream:
            while prefix := stream.read(4):
                if len(prefix) != 4:
                    raise SourceAdapterError("BSON document length is truncated.")
                length = struct.unpack("<i", prefix)[0]
                if length < 5 or length > MAX_BSON_DOCUMENT_BYTES:
                    raise SourceAdapterError("BSON document size is outside the safe limit.")
                remainder = stream.read(length - 4)
                if len(remainder) != length - 4:
                    raise SourceAdapterError("BSON document is truncated.")
                document, consumed = _BsonReader(
                    prefix + remainder,
                    max_depth=max_depth,
                ).document()
                if consumed != length:
                    raise SourceAdapterError("BSON document length is inconsistent.")
                count += 1
                _check_record_limit(count, max_records)
                records.append(_flatten_document(document, max_depth=max_depth))
                if len(records) >= batch_size:
                    if cancel_check is not None:
                        cancel_check()
                    yield RecordBatch("records", tuple(records))
                    records.clear()
        if records:
            if cancel_check is not None:
                cancel_check()
            yield RecordBatch("records", tuple(records))
