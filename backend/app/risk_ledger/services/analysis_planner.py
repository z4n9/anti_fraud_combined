from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from app.risk_ledger.domain.canonical_schema import (
    CANONICAL_SCHEMA_V1,
    AnalysisProfile,
    EntityKind,
)
from app.risk_ledger.domain.contracts import (
    AnalysisPlanContract,
    DatasetInventory,
    DatasetSelection,
    MappingStatus,
    PlanProfileState,
    ProfilePlan,
    RelationshipCandidate,
    SemanticMappingContract,
    SourceInventoryContract,
    TimeRange,
)
from app.risk_ledger.services.semantic_dictionary import normalize_field_name


CLIENT_EVIDENCE_MINIMUM = 2
HISTORICAL_MINIMUM_DAYS = 90

_CLIENT_ENTITIES = {EntityKind.CLIENT, EntityKind.CREDIT, EntityKind.EMPLOYMENT}
_TRANSACTION_ENTITIES = {
    EntityKind.TRANSACTION,
    EntityKind.ACCOUNT,
    EntityKind.COUNTERPARTY,
    EntityKind.DEVICE,
    EntityKind.LOCATION,
}
_NON_EVIDENCE_FIELDS = {
    "client.record_id",
    "client.fraud_label",
    "transaction.record_id",
    "transaction.fraud_label",
}


class AnalysisPlanner:
    def build(
        self,
        inventory: SourceInventoryContract,
        mapping: SemanticMappingContract,
    ) -> AnalysisPlanContract:
        if inventory.analysis_id != mapping.analysis_id:
            raise ValueError("Inventory and mapping belong to different analyses.")

        client_plan = self._client_profile(mapping)
        source_period = self._source_period(inventory, mapping)
        transaction_plan = self._transaction_profile(
            inventory,
            mapping,
            source_period,
        )
        datasets = self._dataset_selections(
            inventory,
            mapping,
            client_planned=client_plan.state == PlanProfileState.PLANNED,
            transaction_planned=(
                transaction_plan.state == PlanProfileState.PLANNED
            ),
        )
        warnings = list(inventory.warnings)
        if not datasets:
            warnings.append("Не удалось определить бизнес-сущность наборов данных.")
        if all(
            profile.state != PlanProfileState.PLANNED
            for profile in (client_plan, transaction_plan)
        ):
            warnings.append("Ни один fraud-профиль нельзя безопасно запустить.")
        return AnalysisPlanContract(
            analysis_id=inventory.analysis_id,
            canonical_schema_version=mapping.canonical_schema_version,
            dictionary_version=mapping.dictionary_version,
            datasets=datasets,
            time_range=source_period,
            profiles=(client_plan, transaction_plan),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def _client_profile(self, mapping: SemanticMappingContract) -> ProfilePlan:
        available = sorted(
            {
                item.canonical_field
                for item in mapping.mappings
                if item.entity in _CLIENT_ENTITIES
                and item.canonical_field not in _NON_EVIDENCE_FIELDS
                and item.status
                in {MappingStatus.APPLIED, MappingStatus.APPLIED_WITH_WARNING}
            }
        )
        if len(available) >= CLIENT_EVIDENCE_MINIMUM:
            state = PlanProfileState.PLANNED
            blocking: tuple[str, ...] = ()
        elif available:
            state = PlanProfileState.BLOCKED
            blocking = (
                "Недостаточно характеристик клиента или кредитной истории: "
                f"распознано {len(available)}, требуется минимум "
                f"{CLIENT_EVIDENCE_MINIMUM}.",
            )
        else:
            state = PlanProfileState.SKIPPED
            blocking = ()
        return ProfilePlan(
            profile=AnalysisProfile.CLIENT_RISK,
            state=state,
            required_fields=(),
            available_fields=tuple(available),
            blocking_reasons=blocking,
            comparison_mode="not_applicable",
        )

    def _transaction_profile(
        self,
        inventory: SourceInventoryContract,
        mapping: SemanticMappingContract,
        source_period: TimeRange | None,
    ) -> ProfilePlan:
        required = CANONICAL_SCHEMA_V1.required_fields(
            AnalysisProfile.TRANSACTION_ANOMALY
        )
        available = sorted(
            {
                item.canonical_field
                for item in mapping.mappings
                if item.entity in _TRANSACTION_ENTITIES
                and item.status
                in {MappingStatus.APPLIED, MappingStatus.APPLIED_WITH_WARNING}
            }
        )
        missing = tuple(
            mapping.missing_required_fields.get(
                AnalysisProfile.TRANSACTION_ANOMALY,
                (),
            )
        )
        evidence = bool(available) or any(
            item.entity in _TRANSACTION_ENTITIES and item.confidence >= 0.6
            for item in mapping.mappings
        ) or any(
            self._transaction_label(dataset.display_label)
            for dataset in inventory.datasets
        )
        if not evidence:
            state = PlanProfileState.SKIPPED
            blocking: tuple[str, ...] = ()
        elif missing:
            state = PlanProfileState.BLOCKED
            blocking = tuple(
                f"Не распознано обязательное поле «{CANONICAL_SCHEMA_V1.field_map()[name].label}» "
                "с высокой уверенностью."
                for name in missing
            )
        else:
            state = PlanProfileState.PLANNED
            blocking = ()
        mode = self._comparison_mode(source_period, available)
        warnings: list[str] = []
        if state == PlanProfileState.PLANNED and source_period is None:
            warnings.append(
                "Период операций не определён; будет использовано когортное сравнение."
            )
        return ProfilePlan(
            profile=AnalysisProfile.TRANSACTION_ANOMALY,
            state=state,
            required_fields=required,
            available_fields=tuple(available),
            blocking_reasons=blocking,
            warnings=tuple(warnings),
            comparison_mode=mode,
        )

    @staticmethod
    def _transaction_label(label: str) -> bool:
        normalized = normalize_field_name(label)
        return any(
            token in normalized
            for token in ("transaction", "operation", "payment", "операц", "платеж")
        )

    @staticmethod
    def _comparison_mode(
        source_period: TimeRange | None,
        available_fields: list[str],
    ) -> str:
        if source_period is None or source_period.start is None or source_period.end is None:
            return "cohort"
        duration_days = (source_period.end - source_period.start).total_seconds() / 86_400
        has_subject = any(
            field
            in {
                "transaction.record_id",
                "account.sender_id",
                "counterparty.record_id",
            }
            for field in available_fields
        )
        return (
            "mixed"
            if duration_days >= HISTORICAL_MINIMUM_DAYS and has_subject
            else "cohort"
        )

    def _source_period(
        self,
        inventory: SourceInventoryContract,
        mapping: SemanticMappingContract,
    ) -> TimeRange | None:
        fields = {
            (dataset.dataset_id, field.source_path): field
            for dataset in inventory.datasets
            for field in dataset.fields
        }
        starts: list[datetime] = []
        ends: list[datetime] = []
        for item in mapping.mappings:
            if (
                item.canonical_field != "transaction.timestamp"
                or item.status != MappingStatus.APPLIED
            ):
                continue
            source = fields.get(
                (item.lineage.source_dataset, item.lineage.source_path)
            )
            if source is None:
                continue
            if source.time_min is not None:
                starts.append(source.time_min)
            if source.time_max is not None:
                ends.append(source.time_max)
        if not starts and not ends:
            return None
        return TimeRange(
            start=min(starts) if starts else None,
            end=max(ends) if ends else None,
        )

    def _dataset_selections(
        self,
        inventory: SourceInventoryContract,
        mapping: SemanticMappingContract,
        *,
        client_planned: bool,
        transaction_planned: bool,
    ) -> tuple[DatasetSelection, ...]:
        scores: dict[str, dict[EntityKind, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        for item in mapping.mappings:
            if item.status in {
                MappingStatus.APPLIED,
                MappingStatus.APPLIED_WITH_WARNING,
            }:
                weight = item.confidence
            elif item.confidence >= 0.6:
                weight = item.confidence * 0.25
            else:
                continue
            scores[item.lineage.source_dataset][item.entity] += weight
        inventory_by_id = {dataset.dataset_id: dataset for dataset in inventory.datasets}
        entities: dict[str, EntityKind] = {}
        for dataset_id, entity_scores in scores.items():
            if not entity_scores:
                continue
            if entity_scores.get(EntityKind.TRANSACTION, 0) >= 1:
                entities[dataset_id] = EntityKind.TRANSACTION
            elif entity_scores.get(EntityKind.CLIENT, 0) > 0:
                entities[dataset_id] = EntityKind.CLIENT
            else:
                entities[dataset_id] = sorted(
                    entity_scores,
                    key=lambda entity: (-entity_scores[entity], entity.value),
                )[0]
        if not entities:
            return ()

        preferred = (
            EntityKind.TRANSACTION
            if transaction_planned
            else EntityKind.CLIENT
            if client_planned
            else None
        )
        primary_id = sorted(
            entities,
            key=lambda dataset_id: (
                0 if preferred is not None and entities[dataset_id] == preferred else 1,
                -inventory_by_id[dataset_id].row_count,
                dataset_id,
            ),
        )[0]
        selections: list[DatasetSelection] = []
        for dataset_id in sorted(entities, key=lambda item: (item != primary_id, item)):
            if dataset_id == primary_id:
                selections.append(
                    DatasetSelection(
                        dataset_id=dataset_id,
                        entity=entities[dataset_id],
                        role="primary",
                    )
                )
                continue
            join = self._join_to_primary(
                inventory_by_id[dataset_id],
                inventory_by_id[primary_id],
                inventory.relationships,
            )
            selections.append(
                DatasetSelection(
                    dataset_id=dataset_id,
                    entity=entities[dataset_id],
                    role="related",
                    join_to_dataset=primary_id if join else None,
                    join_from_field=join[0] if join else None,
                    join_to_field=join[1] if join else None,
                )
            )
        return tuple(selections)

    @staticmethod
    def _join_to_primary(
        dataset: DatasetInventory,
        primary: DatasetInventory,
        relationships: tuple[RelationshipCandidate, ...],
    ) -> tuple[str, str] | None:
        for relation in relationships:
            if (
                relation.from_dataset == dataset.dataset_id
                and relation.to_dataset == primary.dataset_id
            ):
                return relation.from_field, relation.to_field
            if (
                relation.to_dataset == dataset.dataset_id
                and relation.from_dataset == primary.dataset_id
            ):
                return relation.to_field, relation.from_field
        source_identifiers = {
            normalize_field_name(field.source_path): field.source_path
            for field in dataset.fields
            if field.likely_identifier
        }
        primary_identifiers = {
            normalize_field_name(field.source_path): field.source_path
            for field in primary.fields
            if field.likely_identifier
        }
        common = sorted(set(source_identifiers) & set(primary_identifiers))
        if not common:
            return None
        name = common[0]
        return source_identifiers[name], primary_identifiers[name]
