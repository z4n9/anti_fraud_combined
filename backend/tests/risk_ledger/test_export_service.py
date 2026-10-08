from __future__ import annotations

from app.risk_ledger.services.export_service import iter_csv_records, mask_record, safe_csv_cell


def test_csv_cells_neutralize_spreadsheet_formulas() -> None:
    for value in ("=1+1", "+SUM(A1:A2)", "-2+3", "@cmd", "\tformula", "\rformula"):
        assert safe_csv_cell(value).startswith("'")
    assert safe_csv_cell("ordinary") == "ordinary"


def test_masking_hides_sensitive_identifiers_and_csv_is_streamed() -> None:
    record = {
        "record_id": "TX-1",
        "client_id": "CLIENT-123456",
        "sender_account_id": "=DANGEROUS",
        "risk_probability": 0.9,
    }
    masked = mask_record(record)
    assert "CLIENT-123456" not in masked.values()
    payload = "".join(iter_csv_records([record], masked=True))
    assert "CLIENT-123456" not in payload
    assert "=DANGEROUS" not in payload
