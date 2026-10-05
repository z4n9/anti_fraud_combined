from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.training.generate_synthetic_transactions import (
    CSV_FILENAME,
    FIELDS,
    MANIFEST_FILENAME,
    SCENARIOS,
    assert_synthetic_privacy,
    generate_synthetic_dataset,
)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _scenario(rows: list[dict[str, str]], name: str) -> list[dict[str, str]]:
    return [row for row in rows if row["scenario"] == name]


def test_generator_is_byte_reproducible_and_seeded(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    changed = tmp_path / "changed"

    first_manifest = generate_synthetic_dataset(first, seed=77, normal_rows=1_000)
    second_manifest = generate_synthetic_dataset(second, seed=77, normal_rows=1_000)
    changed_manifest = generate_synthetic_dataset(changed, seed=78, normal_rows=1_000)

    assert (first / CSV_FILENAME).read_bytes() == (second / CSV_FILENAME).read_bytes()
    assert (first / MANIFEST_FILENAME).read_bytes() == (second / MANIFEST_FILENAME).read_bytes()
    assert first_manifest["csv"]["sha256"] == _digest(first / CSV_FILENAME)
    assert first_manifest["csv"]["sha256"] != changed_manifest["csv"]["sha256"]


def test_fixture_has_canonical_fields_and_every_control_scenario(tmp_path: Path) -> None:
    output = tmp_path / "fixture"
    manifest = generate_synthetic_dataset(output, normal_rows=1_000)
    rows = _read_rows(output / CSV_FILENAME)

    assert tuple(rows[0]) == FIELDS
    assert len(rows) == manifest["rows"]
    assert len({row["transaction_id"] for row in rows}) == len(rows)
    assert all(float(row["transaction_amount"]) > 0 for row in rows)
    assert all(float(row["balance_before"]) >= 0 for row in rows)
    assert all(row["synthetic_only"] == "1" for row in rows)
    assert Counter(row["scenario"] for row in rows) == manifest["scenario_counts"]
    assert set(SCENARIOS).issubset({row["scenario"] for row in rows})
    assert manifest["column_schema"]["transaction_amount"]["canonical_field"] == "transaction.amount"
    assert manifest["column_schema"]["is_anomaly"]["control_only"] is True
    for name in SCENARIOS:
        controls = manifest["scenarios"][name]["control_transaction_ids"]
        assert controls
        assert controls == [row["transaction_id"] for row in _scenario(rows, name)]


def test_control_rows_encode_expected_anomaly_mechanics(tmp_path: Path) -> None:
    output = tmp_path / "fixture"
    generate_synthetic_dataset(output, normal_rows=1_000)
    rows = _read_rows(output / CSV_FILENAME)

    rapid = _scenario(rows, "rapid_series")
    rapid_times = [datetime.fromisoformat(row["transaction_timestamp"].replace("Z", "+00:00")) for row in rapid]
    assert len(rapid) >= 10
    assert (max(rapid_times) - min(rapid_times)).total_seconds() <= 5 * 60

    structured = _scenario(rows, "structuring")
    assert len(structured) >= 8
    assert len({row["recipient_account_id"] for row in structured}) == 1
    assert sum(float(row["transaction_amount"]) for row in structured) > 800_000

    many_to_one = _scenario(rows, "many_to_one")
    assert len({row["sender_account_id"] for row in many_to_one}) >= 10
    assert len({row["recipient_account_id"] for row in many_to_one}) == 1

    cycle = _scenario(rows, "cycle")
    edges = {(row["sender_account_id"], row["recipient_account_id"]) for row in cycle}
    assert len(edges) == 3
    assert {left for left, _ in edges} == {right for _, right in edges}

    cashout = _scenario(rows, "rapid_cashout")
    inbound = next(row for row in cashout if row["direction"] == "inbound")
    outbound = next(row for row in cashout if row["direction"] == "outbound")
    inbound_time = datetime.fromisoformat(inbound["transaction_timestamp"].replace("Z", "+00:00"))
    outbound_time = datetime.fromisoformat(outbound["transaction_timestamp"].replace("Z", "+00:00"))
    assert 0 < (outbound_time - inbound_time).total_seconds() <= 5 * 60
    assert float(outbound["transaction_amount"]) >= float(inbound["transaction_amount"]) * 0.9

    assert all(row["is_new_recipient"] == "1" for row in _scenario(rows, "new_recipient"))
    assert all(
        datetime.fromisoformat(row["transaction_timestamp"].replace("Z", "+00:00")).hour < 5
        for row in _scenario(rows, "night_activity")
    )


def test_fixture_contains_no_real_personal_data_shapes(tmp_path: Path) -> None:
    output = tmp_path / "fixture"
    manifest = generate_synthetic_dataset(output, normal_rows=1_000)
    rows = _read_rows(output / CSV_FILENAME)
    typed_rows = [
        {
            **row,
            "synthetic_only": int(row["synthetic_only"]),
        }
        for row in rows
    ]

    assert_synthetic_privacy(typed_rows)
    assert manifest["synthetic_only"] is True
    assert manifest["suitable_for_real_quality_claims"] is False
    assert manifest["privacy"]["contains_real_personal_data"] is False
    assert all(
        row["client_id"].startswith("SYN-")
        and row["sender_account_id"].startswith("SYN-")
        and row["recipient_account_id"].startswith("SYN-")
        for row in rows
    )


