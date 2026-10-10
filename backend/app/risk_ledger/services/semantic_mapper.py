from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.risk_ledger.domain.canonical_schema import (
    CANONICAL_SCHEMA_V1,
    AnalysisProfile,
    CanonicalDataType,
    CanonicalFieldDefinition,
    Sensitivity,
)
from app.risk_ledger.domain.contracts import (
    FieldInventory,
    FieldLineage,
    FieldTransformation,
    MappingConfidence,
    MappingStatus,
    PhysicalDataType,
    SemanticFieldMapping,
    SemanticMappingContract,
    SourceInventoryContract,
    TransformationKind,
)
from app.risk_ledger.services.semantic_dictionary import SemanticDictionary, normalize_field_name


@dataclass(frozen=True, slots=True)
class _Rule:
    canonical_field: str
    aliases: tuple[str, ...]


_RULES = (
    _Rule(
        "client.record_id",
        ("record_id", "client_id", "customer_id", "subject_id", "iin", "ид_клиента"),
    ),
    _Rule("client.age_years", ("age", "age_years", "client_age", "customer_age", "возраст")),
    _Rule("client.gender", ("gender", "sex", "пол")),
    _Rule("client.residency", ("residency", "resident_status", "резидентство")),
    _Rule("client.education", ("education", "education_level", "образование")),
    _Rule(
        "client.marital_status",
        ("maritalstatus", "marital_status", "family_status", "семейное_положение"),
    ),
    _Rule(
        "employment.nature",
        ("employmentnature", "employment_nature", "employment_type", "profession", "занятость"),
    ),
    _Rule(
        "employment.tenure_months",
        ("employment_tenure", "tenure_months", "work_months", "стаж_месяцев"),
    ),
    _Rule(
        "credit.requested_amount",
        ("total_amount_kzt", "requested_amount", "loan_amount", "credit_amount", "amount"),
    ),
    _Rule("credit.term_days", ("term", "term_days", "loan_term", "credit_term", "срок")),
    _Rule(
        "credit.contract_count",
        ("num_contracts", "contract_count", "contracts_count", "loan_count"),
    ),
    _Rule(
        "credit.overdue_amount",
        ("overdueamount", "overdue_amount", "past_due_amount", "сумма_просрочки"),
    ),
    _Rule(
        "credit.outstanding_amount",
        ("outstandingamount", "outstanding_amount", "debt_balance", "остаток_долга"),
    ),
    _Rule("credit.debt_to_income", ("dti", "dti3m", "debt_to_income", "долговая_нагрузка")),
    _Rule("client.fraud_label", ("gb_flag", "client_fraud_label", "is_fraud", "fraud_flag")),
    _Rule(
        "transaction.record_id",
        ("transaction_id", "tx_id", "payment_id", "operation_id", "ид_операции"),
    ),
    _Rule(
        "transaction.timestamp",
        (
            "transaction_timestamp",
            "transaction_time",
            "operation_datetime",
            "operation_date",
            "created_at",
            "timestamp",
            "дата_операции",
        ),
    ),
    _Rule(
        "transaction.amount",
        ("transaction_amount", "tx_amount", "payment_sum", "operation_amount", "monetary", "amount", "сумма_операции"),
    ),
    _Rule("transaction.currency", ("currency", "currency_code", "валюта")),
    _Rule("transaction.channel", ("channel", "payment_channel", "operation_channel", "transaction_type", "transactiontype", "канал")),
    _Rule(
        "transaction.direction",
        ("direction", "transaction_direction", "flow_direction", "направление"),
    ),
    _Rule(
        "transaction.is_new_recipient",
        ("is_new_recipient", "new_recipient", "recipient_is_new"),
    ),
    _Rule(
        "account.balance_before",
        ("balance_before", "account_balance_before", "available_balance", "old_bal_initiator", "oldbalinitiator"),
    ),
    _Rule(
        "account.sender_id",
        ("sender_account_id", "source_account_id", "from_account", "initiator", "счет_отправителя"),
    ),
    _Rule(
        "account.recipient_id",
        ("recipient_account_id", "destination_account_id", "to_account", "recipient", "счет_получателя"),
    ),
    _Rule(
        "counterparty.record_id",
        ("counterparty_id", "beneficiary_id", "recipient_id", "контрагент"),
    ),
    _Rule("device.record_id", ("device_id", "session_device_id", "устройство")),
    _Rule("location.country", ("country", "country_code", "operation_country", "страна")),
    _Rule(
        "transaction.fraud_label",
        ("transaction_fraud_label", "tx_fraud_flag", "is_fraud_transaction"),
    ),
)

_GENERIC_ALIASES = {"amount", "id", "date", "time", "timestamp", "status"}
_SECONDARY_ALIASES = {"profession"}
_TRANSACTION_CONTEXT = {
    "transaction_id",
    "tx_id",
    "payment_id",
    "operation_id",
    "transaction_timestamp",
    "transaction_time",
    "operation_datetime",
    "operation_date",
    "sender_account_id",
    "recipient_account_id",
    "currency",
    "channel",
}
_CREDIT_CONTEXT = {
    "term",
    "term_days",
    "loan_term",
    "credit_term",
    "overdueamount",
    "overdue_amount",
    "outstandingamount",
    "outstanding_amount",
    "num_contracts",
    "dti",
    "dti3m",
}


@dataclass(slots=True)
class _Candidate:
    dataset_id: str
    field: FieldInventory
    definition: CanonicalFieldDefinition
    score: float
    reasons: list[str]
    warnings: list[str]


class SemanticSchemaMapper:
    def __init__(self, dictionary: SemanticDictionary | None = None) -> None:
        self.dictionary = dictionary or SemanticDictionary()
        self._definitions = CANONICAL_SCHEMA_V1.field_map()
        self._rules = {rule.canonical_field: rule for rule in _RULES}

    def map(self, inventory: SourceInventoryContract) -> SemanticMappingContract:
        candidates: list[_Candidate] = []
        unmapped: list[str] = []
        for dataset in inventory.datasets:
            normalized_fields = {
                normalize_field_name(field.source_path) for field in dataset.fields
            }
            context = self._context(normalized_fields, dataset.display_label)
            for field in dataset.fields:
                ranked = self._rank(field, context)
                source_key = f"{dataset.dataset_id}.{field.source_path}"
                if not ranked or ranked[0].score < 0.42:
                    unmapped.append(source_key)
                    continue
                top = ranked[0]
                if (
                    len(ranked) > 1
                    and ranked[1].score >= 0.62
                    and top.score - ranked[1].score < 0.08
                ):
                    top.score = min(top.score, 0.59)
                    top.warnings.append(
                        "Поле неоднозначно: несколько канонических смыслов имеют близкую оценку."
                    )
                top.dataset_id = dataset.dataset_id
                candidates.append(top)

        self._resolve_duplicate_targets(candidates)
        mappings = tuple(self._to_mapping(candidate) for candidate in candidates)
        applied_required = {
            mapping.canonical_field
            for mapping in mappings
            if mapping.status == MappingStatus.APPLIED
        }
        missing_required = {
            profile: tuple(
                field
                for field in CANONICAL_SCHEMA_V1.required_fields(profile)
                if field not in applied_required
            )
            for profile in AnalysisProfile
        }
        return SemanticMappingContract(
            analysis_id=inventory.analysis_id,
            canonical_schema_version=CANONICAL_SCHEMA_V1.contract_version,
            dictionary_version=self.dictionary.version_label,
            mappings=mappings,
            unmapped_source_fields=tuple(unmapped),
            missing_required_fields=missing_required,
        )

    def _context(self, fields: set[str], label: str) -> set[str]:
        context: set[str] = set()
        normalized_label = normalize_field_name(label)
        if fields & _TRANSACTION_CONTEXT or any(
            token in normalized_label for token in ("transaction", "operation", "payment", "операц")
        ):
            context.add("transaction")
        if fields & _CREDIT_CONTEXT or any(
            token in normalized_label for token in ("credit", "loan", "contract", "кредит")
        ):
            context.add("credit")
        return context

    def _rank(self, field: FieldInventory, context: set[str]) -> list[_Candidate]:
        normalized = normalize_field_name(field.source_path)
        ranked: list[_Candidate] = []
        trusted = set(self.dictionary.trusted_targets(normalized))
        for canonical_field, definition in self._definitions.items():
            rule = self._rules.get(canonical_field)
            aliases = tuple(normalize_field_name(item) for item in (rule.aliases if rule else ()))
            score, reasons = self._name_score(
                normalized,
                normalize_field_name(canonical_field),
                aliases,
                canonical_field in trusted,
                self.dictionary.entry(normalized, canonical_field) is not None,
            )
            compatibility = self._type_compatibility(field.physical_type, definition.data_type)
            score += compatibility
            if compatibility > 0:
                reasons.append("Физический тип совместим с каноническим полем")
            elif compatibility < 0:
                reasons.append("Физический тип противоречит ожидаемому")
            if definition.entity.value == "transaction" and "transaction" in context:
                score += 0.12
                reasons.append("Набор содержит признаки отдельных операций")
            if definition.entity.value == "credit" and "credit" in context:
                score += 0.12
                reasons.append("Набор содержит кредитные показатели")
            if field.likely_identifier and definition.sensitivity == Sensitivity.IDENTIFIER:
                score += 0.08
                reasons.append("Поле похоже на устойчивый идентификатор")
            if "fraud_label" in canonical_field and field.distinct_count is not None:
                if field.distinct_count <= 2:
                    score += 0.06
                    reasons.append("Кардинальность совместима с бинарной меткой")
                elif field.distinct_count > 10:
                    score -= 0.12
            if field.missing_rate > 0.9:
                score -= 0.08
            ranked.append(
                _Candidate(
                    dataset_id="",
                    field=field,
                    definition=definition,
                    score=max(0, min(score, 1)),
                    reasons=reasons,
                    warnings=[],
                )
            )
        return sorted(ranked, key=lambda item: (-item.score, item.definition.name))

    def _name_score(
        self,
        source: str,
        canonical: str,
        aliases: tuple[str, ...],
        trusted: bool,
        candidate: bool,
    ) -> tuple[float, list[str]]:
        if trusted:
            return 0.82, ["Подтверждённое правило локального словаря"]
        if source == canonical:
            return 0.82, ["Имя совпадает с каноническим полем"]
        if source in aliases:
            base = (
                0.58
                if source in _GENERIC_ALIASES
                else 0.7
                if source in _SECONDARY_ALIASES
                else 0.8
            )
            return base, ["Имя найдено в локальном словаре синонимов"]
        if candidate:
            return 0.52, ["Найдено кандидатное правило локального словаря"]
        source_tokens = set(source.split("_"))
        choices = aliases + (canonical,)
        best = 0.0
        for choice in choices:
            choice_tokens = set(choice.split("_"))
            union = source_tokens | choice_tokens
            jaccard = len(source_tokens & choice_tokens) / len(union) if union else 0
            ratio = SequenceMatcher(None, source, choice).ratio()
            best = max(best, jaccard * 0.56, ratio * 0.48)
        reasons = ["Имя похоже на известный синоним"] if best >= 0.35 else []
        return best, reasons

    @staticmethod
    def _type_compatibility(
        source: PhysicalDataType,
        expected: CanonicalDataType,
    ) -> float:
        if source == PhysicalDataType.NULL:
            return -0.08
        expected_sources = {
            CanonicalDataType.STRING: {
                PhysicalDataType.STRING,
                PhysicalDataType.INTEGER,
            },
            CanonicalDataType.CATEGORY: {
                PhysicalDataType.STRING,
                PhysicalDataType.INTEGER,
                PhysicalDataType.BOOLEAN,
            },
            CanonicalDataType.INTEGER: {PhysicalDataType.INTEGER},
            CanonicalDataType.NUMBER: {
                PhysicalDataType.INTEGER,
                PhysicalDataType.NUMBER,
            },
            CanonicalDataType.BOOLEAN: {
                PhysicalDataType.BOOLEAN,
                PhysicalDataType.INTEGER,
            },
            CanonicalDataType.DATE: {
                PhysicalDataType.DATE,
                PhysicalDataType.DATETIME,
            },
            CanonicalDataType.DATETIME: {
                PhysicalDataType.DATE,
                PhysicalDataType.DATETIME,
            },
        }
        if source in expected_sources[expected]:
            return 0.18
        if source == PhysicalDataType.STRING and expected in {
            CanonicalDataType.NUMBER,
            CanonicalDataType.INTEGER,
            CanonicalDataType.BOOLEAN,
            CanonicalDataType.DATE,
            CanonicalDataType.DATETIME,
        }:
            return 0.08
        return -0.25

    @staticmethod
    def _resolve_duplicate_targets(candidates: list[_Candidate]) -> None:
        grouped: dict[str, list[_Candidate]] = {}
        for candidate in candidates:
            if candidate.score >= 0.6:
                grouped.setdefault(candidate.definition.name, []).append(candidate)
        for group in grouped.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda item: (-item.score, item.field.source_path))
            if group[0].score - group[1].score < 0.03:
                for candidate in group:
                    candidate.score = min(candidate.score, 0.59)
                    candidate.warnings.append(
                        "Несколько исходных полей одинаково претендуют на один смысл."
                    )
            else:
                for candidate in group[1:]:
                    candidate.score = min(candidate.score, 0.59)
                    candidate.warnings.append(
                        "Выбрано более уверенное исходное поле с тем же смыслом."
                    )

    def _to_mapping(self, candidate: _Candidate) -> SemanticFieldMapping:
        score = round(candidate.score, 6)
        confidence = (
            MappingConfidence.HIGH
            if score >= 0.85
            else MappingConfidence.MEDIUM
            if score >= 0.6
            else MappingConfidence.LOW
        )
        required = bool(candidate.definition.required_for)
        if confidence == MappingConfidence.HIGH:
            status = MappingStatus.APPLIED
        elif confidence == MappingConfidence.MEDIUM and not required:
            status = MappingStatus.APPLIED_WITH_WARNING
        else:
            status = MappingStatus.UNUSED
        warnings = list(candidate.warnings)
        if confidence == MappingConfidence.MEDIUM:
            warnings.append(
                "Сопоставление средней уверенности требует контроля качества."
            )
        if required and confidence != MappingConfidence.HIGH:
            warnings.append(
                "Обязательное поле не применяется без высокой уверенности."
            )
        return SemanticFieldMapping(
            canonical_field=candidate.definition.name,
            display_label=candidate.definition.label,
            entity=candidate.definition.entity,
            confidence=score,
            confidence_level=confidence,
            status=status,
            lineage=FieldLineage(
                source_dataset=candidate.dataset_id,
                source_path=candidate.field.source_path,
                canonical_field=candidate.definition.name,
                transformations=self._transformations(
                    candidate.field.physical_type,
                    candidate.definition.data_type,
                ),
                dictionary_version=self.dictionary.version_label,
                reasons=tuple(candidate.reasons or ("Совместимость недостаточна",)),
            ),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _transformations(
        source: PhysicalDataType,
        target: CanonicalDataType,
    ) -> tuple[FieldTransformation, ...]:
        transformations: list[FieldTransformation] = []
        if source == PhysicalDataType.STRING:
            transformations.append(FieldTransformation(kind=TransformationKind.TRIM))
        if source == PhysicalDataType.STRING and target in {
            CanonicalDataType.INTEGER,
            CanonicalDataType.NUMBER,
        }:
            transformations.append(
                FieldTransformation(kind=TransformationKind.PARSE_NUMBER)
            )
        if source == PhysicalDataType.STRING and target in {
            CanonicalDataType.DATE,
            CanonicalDataType.DATETIME,
        }:
            transformations.append(
                FieldTransformation(kind=TransformationKind.PARSE_DATE)
            )
        if target == CanonicalDataType.CATEGORY:
            transformations.append(
                FieldTransformation(kind=TransformationKind.NORMALIZE_CATEGORY)
            )
        return tuple(transformations)
