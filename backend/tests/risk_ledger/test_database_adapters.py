from __future__ import annotations

import hashlib
import sqlite3
import struct
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.risk_ledger.domain.contracts import PhysicalDataType, SourceFormat
from app.risk_ledger.services.database_adapters import (
    BsonSourceAdapter,
    SqlDumpSourceAdapter,
    SqliteSourceAdapter,
)
from app.risk_ledger.services.source_adapters import SourceAdapterError, SourceRecordLimitError


def inspect(adapter, path: Path, source_format: SourceFormat, **kwargs):
    return adapter.inspect(
        path,
        analysis_id="analysis-1",
        filename=path.name,
        source_format=source_format,
        batch_size=2,
        **kwargs,
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sqlite_is_inventoried_read_only_with_keys_views_and_relationships(
    tmp_path: Path,
) -> None:
    source = tmp_path / "bank.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE transactions ("
            "id INTEGER PRIMARY KEY, client_id INTEGER, amount REAL, "
            "FOREIGN KEY(client_id) REFERENCES clients(id))"
        )
        connection.execute("INSERT INTO clients VALUES (1, 'Иван')")
        connection.execute("INSERT INTO transactions VALUES (10, 1, 25.5)")
        connection.execute(
            "CREATE INDEX idx_transactions_client ON transactions(client_id)"
        )
        connection.execute(
            "CREATE VIEW transaction_totals AS "
            "SELECT client_id, SUM(amount) AS total FROM transactions GROUP BY client_id"
        )
    before = _digest(source)

    inventory = inspect(SqliteSourceAdapter(), source, SourceFormat.SQLITE)

    assert _digest(source) == before
    datasets = {dataset.dataset_id: dataset for dataset in inventory.datasets}
    assert set(datasets) == {"clients", "transactions", "transaction_totals"}
    assert datasets["clients"].row_count == 1
    assert datasets["transactions"].primary_key_candidates == ("id",)
    assert any(
        index.name == "idx_transactions_client"
        and index.fields == ("client_id",)
        for index in datasets["transactions"].indexes
    )
    assert inventory.relationships[0].from_dataset == "transactions"
    assert inventory.relationships[0].from_field == "client_id"
    assert inventory.relationships[0].to_dataset == "clients"


def test_sqlite_hides_system_tables_and_preserves_empty_table_schema(
    tmp_path: Path,
) -> None:
    source = tmp_path / "empty.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE TABLE empty_records (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT)"
        )

    inventory = inspect(SqliteSourceAdapter(), source, SourceFormat.SQLITE)

    assert [dataset.dataset_id for dataset in inventory.datasets] == ["empty_records"]
    assert inventory.datasets[0].row_count == 0
    assert [field.source_path for field in inventory.datasets[0].fields] == [
        "id",
        "value",
    ]


@pytest.mark.parametrize(
    ("filename", "dump", "dataset_id"),
    [
        (
            "postgres.sql",
            'CREATE TABLE "public"."tx" ("id" INTEGER, "amount" NUMERIC);\n'
            'INSERT INTO "public"."tx" ("id", "amount") VALUES (1, 10.5), (2, 20);\n',
            "public.tx",
        ),
        (
            "mysql.sql",
            "CREATE TABLE `tx` (`id` INT, `amount` DECIMAL(10,2));\n"
            "INSERT INTO `tx` (`id`, `amount`) VALUES (1, 10.5), (2, 20);\n",
            "tx",
        ),
        (
            "sqlserver.sql",
            "CREATE TABLE [dbo].[tx] ([id] INT, [amount] DECIMAL(10,2));\nGO\n"
            "INSERT INTO [dbo].[tx] ([id], [amount]) VALUES (1, 10.5);\nGO\n"
            "INSERT INTO [dbo].[tx] ([id], [amount]) VALUES (2, 20);\nGO\n",
            "dbo.tx",
        ),
    ],
)
def test_sql_dump_parses_supported_dialects_without_execution(
    tmp_path: Path,
    filename: str,
    dump: str,
    dataset_id: str,
) -> None:
    source = tmp_path / filename
    source.write_text(dump, encoding="utf-8")

    inventory = inspect(SqlDumpSourceAdapter(), source, SourceFormat.SQL_DUMP)

    assert len(inventory.datasets) == 1
    dataset = inventory.datasets[0]
    assert dataset.dataset_id == dataset_id
    assert dataset.row_count == 2
    fields = {field.source_path: field for field in dataset.fields}
    assert fields["id"].physical_type == PhysicalDataType.INTEGER
    assert fields["amount"].physical_type == PhysicalDataType.NUMBER


def test_postgresql_copy_from_stdin_is_parsed_as_data(tmp_path: Path) -> None:
    source = tmp_path / "copy.sql"
    source.write_text(
        "CREATE TABLE tx (id INTEGER, note TEXT);\n"
        "COPY tx (id, note) FROM STDIN;\n"
        "1\tfirst\n"
        "2\t\\N\n"
        "\\.\n",
        encoding="utf-8",
    )

    inventory = inspect(SqlDumpSourceAdapter(), source, SourceFormat.SQL_DUMP)
    fields = {field.source_path: field for field in inventory.datasets[0].fields}

    assert inventory.datasets[0].row_count == 2
    assert fields["note"].nullable is True
    assert fields["note"].missing_rate == 0.5


@pytest.mark.parametrize(
    "dangerous",
    [
        "CREATE FUNCTION steal() RETURNS void AS 'x';",
        "CREATE PROCEDURE run_me AS SELECT 1;",
        "CREATE TRIGGER run_me BEFORE INSERT ON tx EXECUTE x;",
        "EXEC xp_cmdshell 'whoami';",
        "ATTACH DATABASE 'secret.db' AS secret;",
        "COPY tx FROM PROGRAM 'curl attacker';",
        "SELECT 1 INTO OUTFILE '/tmp/result';",
    ],
)
def test_sql_dump_rejects_executable_and_unsafe_constructs(
    tmp_path: Path,
    dangerous: str,
) -> None:
    source = tmp_path / "dangerous.sql"
    source.write_text(dangerous, encoding="utf-8")

    with pytest.raises(SourceAdapterError, match="prohibited|unsupported"):
        inspect(SqlDumpSourceAdapter(), source, SourceFormat.SQL_DUMP)


def _cstring(value: str) -> bytes:
    return value.encode("utf-8") + b"\x00"


def _bson_document(values: dict) -> bytes:
    elements = b"".join(_bson_element(key, value) for key, value in values.items())
    return struct.pack("<i", len(elements) + 5) + elements + b"\x00"


def _bson_element(key: str, value) -> bytes:
    name = _cstring(key)
    if value is None:
        return b"\x0a" + name
    if isinstance(value, bool):
        return b"\x08" + name + bytes([int(value)])
    if isinstance(value, int):
        return b"\x10" + name + struct.pack("<i", value)
    if isinstance(value, float):
        return b"\x01" + name + struct.pack("<d", value)
    if isinstance(value, str):
        encoded = value.encode("utf-8") + b"\x00"
        return b"\x02" + name + struct.pack("<i", len(encoded)) + encoded
    if isinstance(value, datetime):
        milliseconds = int(value.timestamp() * 1000)
        return b"\x09" + name + struct.pack("<q", milliseconds)
    if isinstance(value, dict):
        return b"\x03" + name + _bson_document(value)
    if isinstance(value, list):
        return b"\x04" + name + _bson_document(
            {str(index): item for index, item in enumerate(value)}
        )
    raise TypeError(type(value))


def test_bson_streams_documents_and_flattens_nested_objects(tmp_path: Path) -> None:
    source = tmp_path / "transactions.bson"
    source.write_bytes(
        _bson_document(
            {
                "id": 1,
                "amount": 10.5,
                "created_at": datetime(2026, 9, 1, tzinfo=UTC),
                "client": {"city": "A"},
                "tags": ["mobile", "new"],
            }
        )
        + _bson_document(
            {
                "id": 2,
                "amount": 20.0,
                "created_at": datetime(2026, 9, 2, tzinfo=UTC),
                "client": {"city": "B"},
                "tags": [],
            }
        )
    )

    inventory = inspect(BsonSourceAdapter(), source, SourceFormat.BSON)
    fields = {field.source_path: field for field in inventory.datasets[0].fields}

    assert inventory.datasets[0].row_count == 2
    assert fields["client.city"].physical_type == PhysicalDataType.STRING
    assert fields["tags"].physical_type == PhysicalDataType.ARRAY
    assert fields["created_at"].physical_type == PhysicalDataType.DATETIME


def test_bson_rejects_javascript_and_enforces_limits(tmp_path: Path) -> None:
    javascript = b"alert(1)\x00"
    element = b"\x0d" + _cstring("code") + struct.pack("<i", len(javascript)) + javascript
    prohibited = struct.pack("<i", len(element) + 5) + element + b"\x00"
    source = tmp_path / "javascript.bson"
    source.write_bytes(prohibited)
    with pytest.raises(SourceAdapterError, match="JavaScript"):
        inspect(BsonSourceAdapter(), source, SourceFormat.BSON)

    two_documents = tmp_path / "two.bson"
    two_documents.write_bytes(_bson_document({"id": 1}) + _bson_document({"id": 2}))
    with pytest.raises(SourceRecordLimitError, match="more than 1"):
        inspect(
            BsonSourceAdapter(),
            two_documents,
            SourceFormat.BSON,
            max_records=1,
        )

    nested = tmp_path / "nested.bson"
    nested.write_bytes(_bson_document({"a": {"b": {"c": 1}}}))
    with pytest.raises(SourceAdapterError, match="nesting"):
        inspect(
            BsonSourceAdapter(),
            nested,
            SourceFormat.BSON,
            max_depth=1,
        )


def test_sqlite_and_sql_dump_enforce_global_record_limit(tmp_path: Path) -> None:
    database = tmp_path / "limit.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE one (id INTEGER)")
        connection.execute("CREATE TABLE two (id INTEGER)")
        connection.execute("INSERT INTO one VALUES (1)")
        connection.execute("INSERT INTO two VALUES (2)")
    with pytest.raises(SourceRecordLimitError, match="more than 1"):
        inspect(
            SqliteSourceAdapter(),
            database,
            SourceFormat.SQLITE,
            max_records=1,
        )

    dump = tmp_path / "limit.sql"
    dump.write_text(
        "CREATE TABLE tx (id INT); INSERT INTO tx VALUES (1), (2);",
        encoding="utf-8",
    )
    with pytest.raises(SourceRecordLimitError, match="more than 1"):
        inspect(
            SqlDumpSourceAdapter(),
            dump,
            SourceFormat.SQL_DUMP,
            max_records=1,
        )
