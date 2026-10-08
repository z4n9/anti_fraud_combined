from __future__ import annotations

from pathlib import Path

import pytest

from app.risk_ledger.services.csv_reader import CsvFormatError, inspect_csv, read_csv


@pytest.mark.parametrize("delimiter", [";", ","])
def test_detects_supported_delimiters_and_trims_headers(
    tmp_path: Path,
    delimiter: str,
) -> None:
    csv_path = tmp_path / "input.csv"
    csv_path.write_text(
        f" first {delimiter} second \n1{delimiter}2\n",
        encoding="utf-8",
    )

    frame, metadata = read_csv(csv_path)

    assert metadata.delimiter == delimiter
    assert frame.columns.tolist() == ["first", "second"]
    assert frame.iloc[0].tolist() == ["1", "2"]


def test_rejects_duplicate_headers_after_normalization(tmp_path: Path) -> None:
    csv_path = tmp_path / "duplicate.csv"
    csv_path.write_text("value; value \n1;2\n", encoding="utf-8")

    with pytest.raises(CsvFormatError, match="duplicate columns"):
        inspect_csv(csv_path)


def test_rejects_unknown_delimiter(tmp_path: Path) -> None:
    csv_path = tmp_path / "invalid.csv"
    csv_path.write_text("single_column\nvalue\n", encoding="utf-8")

    with pytest.raises(CsvFormatError, match="delimiter"):
        inspect_csv(csv_path)
