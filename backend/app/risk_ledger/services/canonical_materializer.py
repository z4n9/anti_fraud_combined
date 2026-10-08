from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app.risk_ledger.domain.canonical_schema import EntityKind
from app.risk_ledger.domain.contracts import (
    AnalysisPlanContract,
    MappingStatus,
    SemanticMappingContract,
    SourceFormat,
)
from app.risk_ledger.services.adapter_registry import DEFAULT_SOURCE_ADAPTERS


TRANSACTION_COLUMNS = {
    "transaction.record_id": "transaction_id",
    "client.record_id": "client_id",
    "account.sender_id": "sender_account_id",
    "account.recipient_id": "recipient_account_id",
    "counterparty.record_id": "recipient_account_id",
    "transaction.timestamp": "transaction_timestamp",
    "transaction.amount": "transaction_amount",
    "transaction.currency": "currency",
    "transaction.channel": "channel",
    "transaction.direction": "direction",
    "transaction.is_new_recipient": "is_new_recipient",
    "account.balance_before": "balance_before",
    "device.record_id": "device_id",
    "location.country": "country",
    "transaction.fraud_label": "is_anomaly",
}


@dataclass(frozen=True, slots=True)
class MaterializedProfiles:
    client_frame: pd.DataFrame | None
    transaction_path: Path | None
    transaction_rows: int


def materialize_profiles(
    source_path: str | Path,
    source_format: SourceFormat,
    mapping: SemanticMappingContract,
    plan: AnalysisPlanContract,
    output_dir: str | Path,
    *,
    max_records: int,
    cancel_check,
) -> MaterializedProfiles:
    adapter = DEFAULT_SOURCE_ADAPTERS.get(source_format)
    selected = {item.dataset_id: item.entity for item in plan.datasets}
    mappings: dict[str, list[Any]] = {}
    for item in mapping.mappings:
        if item.status in {MappingStatus.APPLIED, MappingStatus.APPLIED_WITH_WARNING}:
            mappings.setdefault(item.lineage.source_dataset, []).append(item)

    client_records: list[dict[str, Any]] = []
    transaction_records: list[dict[str, Any]] = []
    position = 0
    for batch in adapter.iter_batches(
        source_path,
        max_records=max_records,
        cancel_check=cancel_check,
    ):
        dataset_mappings = mappings.get(batch.dataset_id, [])
        entity = selected.get(batch.dataset_id)
        client_evidence = {
            item.canonical_field
            for item in dataset_mappings
            if item.canonical_field.startswith(("client.", "credit.", "employment."))
            and item.canonical_field
            not in {"client.record_id", "client.fraud_label"}
        }
        # A transaction table normally contains client_id only to link the
        # operation to its owner. That identifier alone must not turn every
        # transaction into a duplicate client profile. Mixed flat files remain
        # supported when they contain at least two actual client/credit fields.
        has_client = entity == EntityKind.CLIENT or len(client_evidence) >= 2
        has_transactions = entity == EntityKind.TRANSACTION or any(
            item.canonical_field.startswith(("transaction.", "account.", "counterparty."))
            for item in dataset_mappings
        )
        for raw in batch.records:
            position += 1
            canonical = {
                item.canonical_field: raw.get(item.lineage.source_path)
                for item in dataset_mappings
            }
            if has_client:
                client = dict(raw)
                client.update(canonical)
                client_records.append(client)
            if has_transactions:
                transaction = {
                    target: canonical.get(source)
                    for source, target in TRANSACTION_COLUMNS.items()
                    if canonical.get(source) not in (None, "")
                }
                transaction.setdefault("transaction_id", f"row-{position:09d}")
                transaction.setdefault("client_id", f"unknown-client-{position:09d}")
                transaction.setdefault("sender_account_id", f"unknown-sender-{position:09d}")
                transaction.setdefault("recipient_account_id", f"unknown-recipient-{position:09d}")
                for control in ("scenario", "synthetic_only"):
                    if control in raw:
                        transaction[control] = raw[control]
                transaction_records.append(transaction)

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    transaction_path: Path | None = None
    if transaction_records:
        frame = pd.DataFrame.from_records(transaction_records)
        frame = frame.sort_values(
            ["transaction_timestamp", "transaction_id"], kind="stable"
        ).reset_index(drop=True)
        transaction_path = output / "canonical-transactions.csv"
        frame.to_csv(transaction_path, index=False, encoding="utf-8", lineterminator="\n")
    return MaterializedProfiles(
        client_frame=pd.DataFrame.from_records(client_records) if client_records else None,
        transaction_path=transaction_path,
        transaction_rows=len(transaction_records),
    )
