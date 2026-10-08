from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Iterator
from itertools import chain
from typing import Any


FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
SENSITIVE_TOKENS = (
    "name", "phone", "email", "iin", "passport", "card", "address",
    "client_id", "customer_id", "account_id", "sender_account", "recipient_account",
)


def safe_csv_cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def mask_identifier(value: Any) -> Any:
    text = str(value)
    if not text:
        return text
    visible = text[-4:] if len(text) > 4 else text[-1:]
    return "*" * max(4, len(text) - len(visible)) + visible


def mask_record(record: dict[str, Any]) -> dict[str, Any]:
    masked: dict[str, Any] = {}
    for key, value in record.items():
        normalized = key.casefold()
        if any(token in normalized for token in SENSITIVE_TOKENS) and value not in (None, ""):
            masked[key] = mask_identifier(value)
        elif isinstance(value, dict):
            masked[key] = mask_record(value)
        elif isinstance(value, list):
            masked[key] = [mask_record(item) if isinstance(item, dict) else item for item in value]
        else:
            masked[key] = value
    return masked


def iter_csv_records(
    records: Iterable[dict[str, Any]],
    *,
    masked: bool = True,
    compact_payload: bool = False,
) -> Iterator[str]:
    iterator = iter(records)
    try:
        first = next(iterator)
    except StopIteration:
        return
    raw_first = first
    first = mask_record(first) if masked else first
    if compact_payload:
        fieldnames = ["profile", "record_id", "risk_probability", "risk_level", "requires_review", "details_json"]

        def compact(item: dict[str, Any]) -> dict[str, Any]:
            prepared = mask_record(item) if masked else item
            return {
                "profile": prepared.get("profile", ""),
                "record_id": prepared.get("record_id", prepared.get("transaction_id", "")),
                "risk_probability": prepared.get("risk_probability", ""),
                "risk_level": prepared.get("risk_level", ""),
                "requires_review": prepared.get("requires_review", ""),
                "details_json": json.dumps(prepared, ensure_ascii=False, separators=(",", ":")),
            }

        convert = compact
    else:
        fieldnames = list(first)
        convert = lambda item: mask_record(item) if masked else item

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    for item in chain((raw_first,), iterator):
        prepared = convert(item)
        writer.writerow({key: safe_csv_cell(value) for key, value in prepared.items()})
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
