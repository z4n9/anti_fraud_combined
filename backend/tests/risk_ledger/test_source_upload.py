from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.risk_ledger.domain.contracts import SourceFormat
from app.risk_ledger.services.source_upload import (
    UploadValidationError,
    cleanup_staging_root,
    detect_source_format,
    prepare_staged_upload,
    remove_staged_upload,
    safe_filename,
    validate_staged_signature,
)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("data.csv", SourceFormat.CSV),
        ("data.json", SourceFormat.JSON),
        ("data.jsonl", SourceFormat.JSONL),
        ("data.ndjson", SourceFormat.NDJSON),
        ("data.sql", SourceFormat.SQL_DUMP),
        ("data.sqlite", SourceFormat.SQLITE),
        ("data.sqlite3", SourceFormat.SQLITE),
        ("data.db", SourceFormat.SQLITE),
        ("data.bson", SourceFormat.BSON),
    ],
)
def test_detects_supported_source_formats(
    filename: str,
    expected: SourceFormat,
) -> None:
    assert detect_source_format(filename) == expected


def test_safe_filename_removes_client_paths_and_rejects_invalid_names() -> None:
    assert safe_filename(r"C:\private\monthly.csv") == "monthly.csv"
    assert safe_filename("../../monthly.csv") == "monthly.csv"
    with pytest.raises(UploadValidationError, match="missing"):
        safe_filename("..")
    with pytest.raises(UploadValidationError, match="control"):
        safe_filename("bad\x00.csv")


@pytest.mark.parametrize(
    ("source_format", "payload"),
    [
        (SourceFormat.CSV, b"id;amount\n1;20\n"),
        (SourceFormat.JSON, b'[{"id": 1}]'),
        (SourceFormat.JSONL, b'{"id": 1}\n{"id": 2}\n'),
        (SourceFormat.NDJSON, b'{"id": 1}\n'),
        (
            SourceFormat.SQL_DUMP,
            b"CREATE TABLE tx (id INTEGER); INSERT INTO tx VALUES (1);",
        ),
        (SourceFormat.SQLITE, b"SQLite format 3\x00"),
        (SourceFormat.BSON, b"\x05\x00\x00\x00\x00"),
    ],
)
def test_validates_supported_file_signatures(
    tmp_path: Path,
    source_format: SourceFormat,
    payload: bytes,
) -> None:
    source = tmp_path / "source"
    source.write_bytes(payload)
    validate_staged_signature(source, source_format)


def test_signature_mismatch_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "fake.sqlite"
    source.write_text("not a database", encoding="utf-8")
    with pytest.raises(UploadValidationError) as error:
        validate_staged_signature(source, SourceFormat.SQLITE)
    assert error.value.code == "invalid_file_signature"


def test_staging_is_isolated_and_cleanup_only_removes_analysis_directories(
    tmp_path: Path,
) -> None:
    incoming = tmp_path / "_incoming"
    incoming.mkdir()
    first_id = str(uuid.uuid4())
    second_id = str(uuid.uuid4())
    first = prepare_staged_upload(incoming, first_id, "month.csv")
    second = prepare_staged_upload(incoming, second_id, "month.json")
    first.write_bytes(b"id\n1\n")
    second.write_bytes(b"[]")
    preserved = incoming / "do-not-remove"
    preserved.mkdir()

    remove_staged_upload(incoming, first_id)
    assert not first.parent.exists()
    assert second.parent.exists()

    assert cleanup_staging_root(incoming) == [second_id]
    assert preserved.exists()
