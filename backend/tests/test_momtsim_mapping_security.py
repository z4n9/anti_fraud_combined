"""Independent bounds for optional MoMTSim aliases and identifier mapping."""
import pytest

from app.risk_ledger.domain.contracts import (
    DatasetInventory, FieldInventory, MappingStatus, PhysicalDataType,
    SourceFormat, SourceInventoryContract,
)
from app.risk_ledger.services.semantic_mapper import SemanticSchemaMapper


def mapped(*fields):
    inventory = SourceInventoryContract(
        analysis_id="alias-security", filename="momtsim.csv",
        source_format=SourceFormat.CSV, file_size_bytes=100,
        datasets=(DatasetInventory(dataset_id="transactions", display_label="Transactions",
            row_count=10, fields=tuple(FieldInventory(
                source_path=name, physical_type=physical_type, nullable=False,
                missing_rate=0, distinct_count=10,
            ) for name, physical_type in fields)),),
    )
    return SemanticSchemaMapper().map(inventory)


@pytest.mark.parametrize("source,target,physical", [
    ("initiator", "account.sender_id", PhysicalDataType.INTEGER),
    ("recipient", "account.recipient_id", PhysicalDataType.INTEGER),
    ("oldBalInitiator", "account.balance_before", PhysicalDataType.NUMBER),
    ("transactionType", "transaction.channel", PhysicalDataType.STRING),
])
def test_momtsim_exact_aliases_preserve_their_meaning(source, target, physical):
    result = mapped((source, physical))
    mapping = next(item for item in result.mappings if item.canonical_field == target)
    assert mapping.status == MappingStatus.APPLIED
    assert mapping.lineage.source_path == source


def test_unknown_mixed_identifier_is_not_high_confidence():
    result = mapped(("initiator", PhysicalDataType.MIXED))
    assert not any(item.canonical_field == "account.sender_id"
                   and item.status == MappingStatus.APPLIED for item in result.mappings)


def test_raw_step_and_post_transfer_balance_do_not_become_historical_features():
    result = mapped(("step", PhysicalDataType.INTEGER),
                    ("newBalInitiator", PhysicalDataType.NUMBER))
    assert not any(item.canonical_field in {"transaction.timestamp", "account.balance_before"}
                   and item.status == MappingStatus.APPLIED for item in result.mappings)


def test_duplicate_sender_aliases_require_disambiguation():
    result = mapped(("initiator", PhysicalDataType.INTEGER),
                    ("sender_account_id", PhysicalDataType.INTEGER))
    assert not any(item.canonical_field == "account.sender_id"
                   and item.status == MappingStatus.APPLIED for item in result.mappings)
