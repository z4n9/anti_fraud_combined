from __future__ import annotations

from datetime import UTC, datetime

from app.risk_ledger.domain.canonical_schema import AnalysisProfile, EntityKind
from app.risk_ledger.domain.contracts import (
    DatasetInventory,
    FieldInventory,
    PhysicalDataType,
    PlanProfileState,
    RelationshipCandidate,
    RelationshipKind,
    SourceFormat,
    SourceInventoryContract,
)
from app.risk_ledger.services.analysis_planner import AnalysisPlanner
from app.risk_ledger.services.semantic_mapper import SemanticSchemaMapper


def field(
    name: str,
    physical_type: PhysicalDataType,
    *,
    identifier: bool = False,
    time_min: datetime | None = None,
    time_max: datetime | None = None,
) -> FieldInventory:
    return FieldInventory(
        source_path=name,
        physical_type=physical_type,
        nullable=False,
        missing_rate=0,
        distinct_count=100,
        likely_identifier=identifier,
        time_min=time_min,
        time_max=time_max,
    )


def source(
    *datasets: DatasetInventory,
    relationships: tuple[RelationshipCandidate, ...] = (),
) -> SourceInventoryContract:
    return SourceInventoryContract(
        analysis_id="analysis-1",
        filename="source.sqlite",
        source_format=SourceFormat.SQLITE,
        file_size_bytes=1024,
        datasets=datasets,
        relationships=relationships,
    )


def dataset(
    dataset_id: str,
    label: str,
    *fields: FieldInventory,
    rows: int = 100,
) -> DatasetInventory:
    return DatasetInventory(
        dataset_id=dataset_id,
        display_label=label,
        row_count=rows,
        fields=fields,
    )


def build_plan(inventory: SourceInventoryContract):
    mapping = SemanticSchemaMapper().map(inventory)
    return AnalysisPlanner().build(inventory, mapping)


def profile(plan, target: AnalysisProfile):
    return next(item for item in plan.profiles if item.profile == target)


def test_routes_client_dataset_only_to_client_profile() -> None:
    inventory = source(
        dataset(
            "clients",
            "Кредитные анкеты",
            field("AGE", PhysicalDataType.INTEGER),
            field("total_amount_kzt", PhysicalDataType.NUMBER),
            field("term", PhysicalDataType.INTEGER),
            field("overdueamount", PhysicalDataType.NUMBER),
        )
    )

    plan = build_plan(inventory)

    assert profile(plan, AnalysisProfile.CLIENT_RISK).state == PlanProfileState.PLANNED
    assert (
        profile(plan, AnalysisProfile.TRANSACTION_ANOMALY).state
        == PlanProfileState.SKIPPED
    )
    assert plan.datasets[0].entity == EntityKind.CLIENT
    assert plan.datasets[0].role == "primary"
    assert plan.time_range is None


def test_routes_monthly_transactions_to_cohort_profile_and_detects_period() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 8, 31, 23, 59, tzinfo=UTC)
    inventory = source(
        dataset(
            "transactions",
            "Операции за август",
            field("operation_id", PhysicalDataType.STRING, identifier=True),
            field(
                "operation_datetime",
                PhysicalDataType.DATETIME,
                time_min=start,
                time_max=end,
            ),
            field("payment_sum", PhysicalDataType.NUMBER),
            field("currency_code", PhysicalDataType.STRING),
        )
    )

    plan = build_plan(inventory)
    transaction = profile(plan, AnalysisProfile.TRANSACTION_ANOMALY)

    assert transaction.state == PlanProfileState.PLANNED
    assert transaction.comparison_mode == "cohort"
    assert profile(plan, AnalysisProfile.CLIENT_RISK).state == PlanProfileState.SKIPPED
    assert plan.time_range is not None
    assert plan.time_range.start == start
    assert plan.time_range.end == end
    assert plan.datasets[0].entity == EntityKind.TRANSACTION


def test_long_transaction_history_selects_mixed_comparison() -> None:
    inventory = source(
        dataset(
            "transactions",
            "Transactions",
            field("transaction_id", PhysicalDataType.STRING, identifier=True),
            field(
                "transaction_timestamp",
                PhysicalDataType.DATETIME,
                time_min=datetime(2026, 1, 1, tzinfo=UTC),
                time_max=datetime(2026, 6, 30, tzinfo=UTC),
            ),
            field("transaction_amount", PhysicalDataType.NUMBER),
        )
    )

    transaction = profile(
        build_plan(inventory),
        AnalysisProfile.TRANSACTION_ANOMALY,
    )

    assert transaction.state == PlanProfileState.PLANNED
    assert transaction.comparison_mode == "mixed"


def test_missing_required_transaction_field_blocks_only_transaction_profile() -> None:
    inventory = source(
        dataset(
            "mixed",
            "Клиенты и операции",
            field("AGE", PhysicalDataType.INTEGER),
            field("loan_amount", PhysicalDataType.NUMBER),
            field(
                "operation_datetime",
                PhysicalDataType.DATETIME,
                time_min=datetime(2026, 8, 1, tzinfo=UTC),
                time_max=datetime(2026, 8, 31, tzinfo=UTC),
            ),
        )
    )

    plan = build_plan(inventory)
    client = profile(plan, AnalysisProfile.CLIENT_RISK)
    transaction = profile(plan, AnalysisProfile.TRANSACTION_ANOMALY)

    assert client.state == PlanProfileState.PLANNED
    assert transaction.state == PlanProfileState.BLOCKED
    assert any("Сумма операции" in reason for reason in transaction.blocking_reasons)
    assert not client.blocking_reasons


def test_unknown_schema_skips_profiles_without_guessing() -> None:
    inventory = source(
        dataset(
            "unknown",
            "Unknown",
            field("opaque_payload", PhysicalDataType.OBJECT),
            field("random_note", PhysicalDataType.STRING),
        )
    )

    plan = build_plan(inventory)

    assert all(item.state == PlanProfileState.SKIPPED for item in plan.profiles)
    assert not plan.datasets
    assert any("Ни один" in warning for warning in plan.warnings)


def test_mixed_source_selects_transaction_primary_and_explicit_join() -> None:
    clients = dataset(
        "clients",
        "Clients",
        field("client_id", PhysicalDataType.STRING, identifier=True),
        field("age", PhysicalDataType.INTEGER),
        field("credit_amount", PhysicalDataType.NUMBER),
    )
    transactions = dataset(
        "transactions",
        "Transactions",
        field("operation_id", PhysicalDataType.STRING, identifier=True),
        field("client_id", PhysicalDataType.STRING, identifier=True),
        field(
            "operation_datetime",
            PhysicalDataType.DATETIME,
            time_min=datetime(2026, 8, 1, tzinfo=UTC),
            time_max=datetime(2026, 8, 31, tzinfo=UTC),
        ),
        field("operation_amount", PhysicalDataType.NUMBER),
        rows=1000,
    )
    inventory = source(
        clients,
        transactions,
        relationships=(
            RelationshipCandidate(
                from_dataset="transactions",
                from_field="client_id",
                to_dataset="clients",
                to_field="client_id",
                kind=RelationshipKind.FOREIGN_KEY,
                confidence=1,
            ),
        ),
    )

    plan = build_plan(inventory)
    selections = {item.dataset_id: item for item in plan.datasets}

    assert profile(plan, AnalysisProfile.CLIENT_RISK).state == PlanProfileState.PLANNED
    assert (
        profile(plan, AnalysisProfile.TRANSACTION_ANOMALY).state
        == PlanProfileState.PLANNED
    )
    assert selections["transactions"].role == "primary"
    assert selections["transactions"].entity == EntityKind.TRANSACTION
    assert selections["clients"].role == "related"
    assert selections["clients"].join_to_dataset == "transactions"
    assert selections["clients"].join_from_field == "client_id"
    assert selections["clients"].join_to_field == "client_id"


def test_plan_is_reproducible_and_serializes_identically() -> None:
    inventory = source(
        dataset(
            "transactions",
            "Transactions",
            field("transaction_timestamp", PhysicalDataType.DATETIME),
            field("transaction_amount", PhysicalDataType.NUMBER),
        )
    )
    mapping = SemanticSchemaMapper().map(inventory)
    planner = AnalysisPlanner()

    first = planner.build(inventory, mapping)
    second = planner.build(inventory, mapping)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


def test_common_identifier_is_used_as_inferred_join() -> None:
    clients = dataset(
        "clients",
        "Clients",
        field("client_id", PhysicalDataType.STRING, identifier=True),
        field("age", PhysicalDataType.INTEGER),
        field("loan_amount", PhysicalDataType.NUMBER),
    )
    operations = dataset(
        "operations",
        "Operations",
        field("client_id", PhysicalDataType.STRING, identifier=True),
        field("operation_datetime", PhysicalDataType.DATETIME),
        field("payment_sum", PhysicalDataType.NUMBER),
    )

    plan = build_plan(source(clients, operations))
    related = next(item for item in plan.datasets if item.role == "related")

    assert related.join_from_field == "client_id"
    assert related.join_to_field == "client_id"
