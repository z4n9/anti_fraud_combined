from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from app.risk_ledger.domain.contracts import SourceFormat
from app.risk_ledger.services import source_adapters
from app.risk_ledger.services.session_store import SessionStore
from app.risk_ledger.services.source_adapters import (
    JsonLinesSourceAdapter,
    JsonSourceAdapter,
    SourceAdapterError,
)
from app.risk_ledger.services.source_upload import UploadValidationError, validate_staged_signature


def test_json_array_rejects_a_single_unbounded_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_adapters, "MAX_TEXT_RECORD_CHARS", 64)
    source = tmp_path / "oversized.json"
    source.write_text('[{"payload":"' + "x" * 80 + '"}]', encoding="utf-8")

    with pytest.raises(SourceAdapterError, match="per-record size"):
        list(JsonSourceAdapter().iter_batches(source, batch_size=1))


def test_json_lines_rejects_an_unbounded_physical_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_adapters, "MAX_TEXT_RECORD_CHARS", 64)
    source = tmp_path / "oversized.jsonl"
    source.write_text('{"payload":"' + "x" * 80 + '"}\n', encoding="utf-8")

    with pytest.raises(SourceAdapterError, match="line 1.*safe size"):
        list(JsonLinesSourceAdapter().iter_batches(source, batch_size=1))


def test_signature_check_rejects_binary_polyglot_as_csv(tmp_path: Path) -> None:
    source = tmp_path / "archive.csv"
    source.write_bytes(b"PK\x03\x04\x00payload")

    with pytest.raises(UploadValidationError, match="does not match"):
        validate_staged_signature(source, SourceFormat.CSV)


def test_legacy_report_neutralizes_spreadsheet_formulas(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session_id = store.create_session(
        [
            {
                "record_id": "row-1",
                "risk_probability": 0.9,
                "risk_level": "critical",
                "requires_review": True,
                "source_note": "=HYPERLINK(\"https://example.invalid\")",
                "explanation_factors": [],
                "analysis_warnings": [],
            }
        ],
        threshold=0.5,
    )

    rows = list(csv.DictReader(io.StringIO("".join(store.iter_csv(session_id)))))

    assert rows[0]["source_note"].startswith("'=")


@pytest.mark.skip(reason="The original standalone FraudBanc Compose deployment is outside the integrated local application stage.")
def test_local_compose_exposes_services_only_on_loopback() -> None:
    compose = (Path(__file__).parents[3] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert '127.0.0.1:8000:8000' in compose
    assert '127.0.0.1:8088:80' in compose
    assert '"0.0.0.0:' not in compose
