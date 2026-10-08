from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


CANONICAL_SCHEMA_VERSION = "1.0"


class AnalysisProfile(StrEnum):
    CLIENT_RISK = "client_risk"
    TRANSACTION_ANOMALY = "transaction_anomaly"


class EntityKind(StrEnum):
    CLIENT = "client"
    EMPLOYMENT = "employment"
    CREDIT = "credit"
    ACCOUNT = "account"
    TRANSACTION = "transaction"
    COUNTERPARTY = "counterparty"
    DEVICE = "device"
    LOCATION = "location"


class CanonicalDataType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    CATEGORY = "category"
    DATE = "date"
    DATETIME = "datetime"


class Sensitivity(StrEnum):
    NORMAL = "normal"
    IDENTIFIER = "identifier"
    PERSONAL = "personal"
    FINANCIAL = "financial"


class CanonicalEntityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: EntityKind
    label: str
    description: str


class CanonicalFieldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    label: str
    description: str
    entity: EntityKind
    data_type: CanonicalDataType
    sensitivity: Sensitivity = Sensitivity.NORMAL
    required_for: tuple[AnalysisProfile, ...] = ()
    may_be_derived: bool = False


class CanonicalSchemaContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["1.0"] = CANONICAL_SCHEMA_VERSION
    schema_id: Literal["risk-ledger-canonical"] = "risk-ledger-canonical"
    entities: tuple[CanonicalEntityDefinition, ...]
    fields: tuple[CanonicalFieldDefinition, ...]

    def field_map(self) -> dict[str, CanonicalFieldDefinition]:
        return {field.name: field for field in self.fields}

    def required_fields(self, profile: AnalysisProfile) -> tuple[str, ...]:
        return tuple(
            field.name for field in self.fields if profile in field.required_for
        )


CANONICAL_SCHEMA_V1 = CanonicalSchemaContract(
    entities=(
        CanonicalEntityDefinition(
            kind=EntityKind.CLIENT,
            label="Клиент",
            description="Физическое или юридическое лицо, для которого оценивается риск.",
        ),
        CanonicalEntityDefinition(
            kind=EntityKind.EMPLOYMENT,
            label="Занятость",
            description="Характеристики работы и источника дохода клиента.",
        ),
        CanonicalEntityDefinition(
            kind=EntityKind.CREDIT,
            label="Кредитная история",
            description="Договоры, долговая нагрузка и просрочки клиента.",
        ),
        CanonicalEntityDefinition(
            kind=EntityKind.ACCOUNT,
            label="Счёт",
            description="Банковский счёт или иной платёжный источник.",
        ),
        CanonicalEntityDefinition(
            kind=EntityKind.TRANSACTION,
            label="Операция",
            description="Отдельное движение денежных средств.",
        ),
        CanonicalEntityDefinition(
            kind=EntityKind.COUNTERPARTY,
            label="Контрагент",
            description="Получатель или отправитель денежных средств.",
        ),
        CanonicalEntityDefinition(
            kind=EntityKind.DEVICE,
            label="Устройство",
            description="Устройство или сессия, связанные с операцией.",
        ),
        CanonicalEntityDefinition(
            kind=EntityKind.LOCATION,
            label="Местоположение",
            description="Географический контекст операции.",
        ),
    ),
    fields=(
        CanonicalFieldDefinition(
            name="client.record_id",
            label="Идентификатор клиента",
            description="Устойчивый идентификатор клиента или локально созданный идентификатор записи.",
            entity=EntityKind.CLIENT,
            data_type=CanonicalDataType.STRING,
            sensitivity=Sensitivity.IDENTIFIER,
            may_be_derived=True,
        ),
        CanonicalFieldDefinition(
            name="client.age_years",
            label="Возраст клиента",
            description="Возраст клиента в полных годах.",
            entity=EntityKind.CLIENT,
            data_type=CanonicalDataType.NUMBER,
            sensitivity=Sensitivity.PERSONAL,
        ),
        CanonicalFieldDefinition(
            name="client.gender",
            label="Пол клиента",
            description="Категория пола в кодировке источника.",
            entity=EntityKind.CLIENT,
            data_type=CanonicalDataType.CATEGORY,
            sensitivity=Sensitivity.PERSONAL,
        ),
        CanonicalFieldDefinition(
            name="client.residency",
            label="Статус резидентства",
            description="Категория резидентства клиента.",
            entity=EntityKind.CLIENT,
            data_type=CanonicalDataType.CATEGORY,
            sensitivity=Sensitivity.PERSONAL,
        ),
        CanonicalFieldDefinition(
            name="client.education",
            label="Уровень образования",
            description="Категория образования клиента.",
            entity=EntityKind.CLIENT,
            data_type=CanonicalDataType.CATEGORY,
            sensitivity=Sensitivity.PERSONAL,
        ),
        CanonicalFieldDefinition(
            name="client.marital_status",
            label="Семейное положение",
            description="Категория семейного положения клиента.",
            entity=EntityKind.CLIENT,
            data_type=CanonicalDataType.CATEGORY,
            sensitivity=Sensitivity.PERSONAL,
        ),
        CanonicalFieldDefinition(
            name="employment.nature",
            label="Характер занятости",
            description="Тип или форма занятости клиента.",
            entity=EntityKind.EMPLOYMENT,
            data_type=CanonicalDataType.CATEGORY,
        ),
        CanonicalFieldDefinition(
            name="employment.tenure_months",
            label="Стаж работы",
            description="Продолжительность текущей занятости в месяцах.",
            entity=EntityKind.EMPLOYMENT,
            data_type=CanonicalDataType.NUMBER,
        ),
        CanonicalFieldDefinition(
            name="credit.requested_amount",
            label="Сумма финансирования",
            description="Запрошенная или выданная сумма финансирования.",
            entity=EntityKind.CREDIT,
            data_type=CanonicalDataType.NUMBER,
            sensitivity=Sensitivity.FINANCIAL,
        ),
        CanonicalFieldDefinition(
            name="credit.term_days",
            label="Срок финансирования",
            description="Срок договора в днях.",
            entity=EntityKind.CREDIT,
            data_type=CanonicalDataType.NUMBER,
        ),
        CanonicalFieldDefinition(
            name="credit.contract_count",
            label="Количество договоров",
            description="Количество известных кредитных договоров клиента.",
            entity=EntityKind.CREDIT,
            data_type=CanonicalDataType.NUMBER,
        ),
        CanonicalFieldDefinition(
            name="credit.overdue_amount",
            label="Сумма просрочки",
            description="Текущая или агрегированная сумма просроченной задолженности.",
            entity=EntityKind.CREDIT,
            data_type=CanonicalDataType.NUMBER,
            sensitivity=Sensitivity.FINANCIAL,
        ),
        CanonicalFieldDefinition(
            name="credit.outstanding_amount",
            label="Остаток задолженности",
            description="Текущий непогашенный остаток по обязательствам.",
            entity=EntityKind.CREDIT,
            data_type=CanonicalDataType.NUMBER,
            sensitivity=Sensitivity.FINANCIAL,
        ),
        CanonicalFieldDefinition(
            name="credit.debt_to_income",
            label="Долговая нагрузка",
            description="Отношение долговых платежей к доступному доходу.",
            entity=EntityKind.CREDIT,
            data_type=CanonicalDataType.NUMBER,
            may_be_derived=True,
        ),
        CanonicalFieldDefinition(
            name="client.fraud_label",
            label="Подтверждённое мошенничество клиента",
            description="Целевая метка для оценки и обучения; никогда не является входным признаком.",
            entity=EntityKind.CLIENT,
            data_type=CanonicalDataType.BOOLEAN,
            sensitivity=Sensitivity.PERSONAL,
        ),
        CanonicalFieldDefinition(
            name="transaction.record_id",
            label="Идентификатор операции",
            description="Устойчивый идентификатор операции или локально созданный идентификатор.",
            entity=EntityKind.TRANSACTION,
            data_type=CanonicalDataType.STRING,
            sensitivity=Sensitivity.IDENTIFIER,
            may_be_derived=True,
        ),
        CanonicalFieldDefinition(
            name="transaction.timestamp",
            label="Дата и время операции",
            description="Момент выполнения денежной операции.",
            entity=EntityKind.TRANSACTION,
            data_type=CanonicalDataType.DATETIME,
            required_for=(AnalysisProfile.TRANSACTION_ANOMALY,),
        ),
        CanonicalFieldDefinition(
            name="transaction.amount",
            label="Сумма операции",
            description="Абсолютная сумма отдельной денежной операции.",
            entity=EntityKind.TRANSACTION,
            data_type=CanonicalDataType.NUMBER,
            sensitivity=Sensitivity.FINANCIAL,
            required_for=(AnalysisProfile.TRANSACTION_ANOMALY,),
        ),
        CanonicalFieldDefinition(
            name="transaction.currency",
            label="Валюта операции",
            description="Валюта суммы операции.",
            entity=EntityKind.TRANSACTION,
            data_type=CanonicalDataType.CATEGORY,
        ),
        CanonicalFieldDefinition(
            name="transaction.channel",
            label="Канал операции",
            description="Канал выполнения операции: приложение, банкомат, отделение или другой канал.",
            entity=EntityKind.TRANSACTION,
            data_type=CanonicalDataType.CATEGORY,
        ),
        CanonicalFieldDefinition(
            name="transaction.direction",
            label="Направление операции",
            description="Направление денежного потока относительно клиента: входящее или исходящее.",
            entity=EntityKind.TRANSACTION,
            data_type=CanonicalDataType.CATEGORY,
        ),
        CanonicalFieldDefinition(
            name="transaction.is_new_recipient",
            label="Новый получатель",
            description="Признак отсутствия получателя в известной истории клиента.",
            entity=EntityKind.TRANSACTION,
            data_type=CanonicalDataType.BOOLEAN,
            may_be_derived=True,
        ),
        CanonicalFieldDefinition(
            name="account.balance_before",
            label="Баланс до операции",
            description="Доступный баланс счёта непосредственно перед операцией.",
            entity=EntityKind.ACCOUNT,
            data_type=CanonicalDataType.NUMBER,
            sensitivity=Sensitivity.FINANCIAL,
        ),
        CanonicalFieldDefinition(
            name="account.sender_id",
            label="Счёт отправителя",
            description="Идентификатор счёта или источника средств.",
            entity=EntityKind.ACCOUNT,
            data_type=CanonicalDataType.STRING,
            sensitivity=Sensitivity.IDENTIFIER,
        ),
        CanonicalFieldDefinition(
            name="account.recipient_id",
            label="Счёт получателя",
            description="Идентификатор счёта назначения средств.",
            entity=EntityKind.ACCOUNT,
            data_type=CanonicalDataType.STRING,
            sensitivity=Sensitivity.IDENTIFIER,
        ),
        CanonicalFieldDefinition(
            name="counterparty.record_id",
            label="Идентификатор контрагента",
            description="Локальный или исходный идентификатор второй стороны операции.",
            entity=EntityKind.COUNTERPARTY,
            data_type=CanonicalDataType.STRING,
            sensitivity=Sensitivity.IDENTIFIER,
        ),
        CanonicalFieldDefinition(
            name="device.record_id",
            label="Идентификатор устройства",
            description="Обезличенный идентификатор устройства или сессии.",
            entity=EntityKind.DEVICE,
            data_type=CanonicalDataType.STRING,
            sensitivity=Sensitivity.IDENTIFIER,
        ),
        CanonicalFieldDefinition(
            name="location.country",
            label="Страна операции",
            description="Страна выполнения или назначения операции.",
            entity=EntityKind.LOCATION,
            data_type=CanonicalDataType.CATEGORY,
            sensitivity=Sensitivity.PERSONAL,
        ),
        CanonicalFieldDefinition(
            name="transaction.fraud_label",
            label="Подтверждённое мошенничество по операции",
            description="Необязательная целевая метка для оценки; не является входным признаком.",
            entity=EntityKind.TRANSACTION,
            data_type=CanonicalDataType.BOOLEAN,
            sensitivity=Sensitivity.FINANCIAL,
        ),
    ),
)
