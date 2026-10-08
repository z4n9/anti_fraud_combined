from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from app.risk_ledger.domain.canonical_schema import CANONICAL_SCHEMA_V1
from app.risk_ledger.services.semantic_dictionary import normalize_field_name


CLIENT_FEATURE_SCHEMA_VERSION = "1.0"
SHAP_DICTIONARY_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class ClientFeatureDefinition:
    name: str
    aliases: tuple[str, ...]
    label: str
    description: str


_CORE_ALIASES: dict[str, tuple[str, ...]] = {
    "client.age_years": ("AGE", "age", "age_years", "client_age", "customer_age", "возраст"),
    "client.gender": ("GENDER", "gender", "sex", "пол"),
    "client.residency": ("RESIDENCY", "residency", "resident_status", "резидентство"),
    "client.education": ("EDUCATION", "education", "education_level", "образование"),
    "client.marital_status": (
        "MARITALSTATUS",
        "marital_status",
        "family_status",
        "семейное_положение",
    ),
    "employment.nature": (
        "EMPLOYMENTNATURE",
        "employment_nature",
        "employment_type",
        "занятость",
    ),
    "employment.tenure_months": (
        "EMPLOYMENTTENURE",
        "employment_tenure",
        "tenure_months",
        "work_months",
        "стаж_месяцев",
    ),
    "credit.requested_amount": (
        "total_amount_kzt",
        "requested_amount",
        "loan_amount",
        "credit_amount",
    ),
    "credit.term_days": ("term", "term_days", "loan_term", "credit_term", "срок"),
    "credit.contract_count": (
        "NUM_CONTRACTS",
        "num_contracts",
        "contract_count",
        "contracts_count",
        "loan_count",
    ),
    "credit.overdue_amount": (
        "overdueamount",
        "overdue_amount",
        "past_due_amount",
        "сумма_просрочки",
    ),
    "credit.outstanding_amount": (
        "outstandingamount",
        "outstanding_amount",
        "debt_balance",
        "остаток_долга",
    ),
    "credit.debt_to_income": (
        "DTI3M",
        "dti",
        "debt_to_income",
        "долговая_нагрузка",
    ),
}

_CANONICAL_DEFINITIONS = CANONICAL_SCHEMA_V1.field_map()
_ALIAS_TO_CORE = {
    normalize_field_name(alias): canonical
    for canonical, aliases in _CORE_ALIASES.items()
    for alias in aliases
}


def _fallback_name(source_name: str) -> str:
    normalized = normalize_field_name(source_name)
    if not normalized:
        raise ValueError("An empty feature name cannot be canonicalized.")
    credit_prefixes = (
        "cnt_",
        "month_overdue_",
        "num_contract",
        "totalamount_",
        "overdue",
        "outstanding",
        "instalment",
        "dti",
        "as3m",
    )
    namespace = "credit.history" if normalized.startswith(credit_prefixes) else "client.attributes"
    return f"{namespace}.{normalized}"


def _fallback_label(source_name: str) -> tuple[str, str]:
    normalized = normalize_field_name(source_name)
    overdue = re.fullmatch(r"month_overdue_([ca])(\d+)", normalized)
    if overdue:
        kind, month = overdue.groups()
        label = (
            f"Просрочки — месяц {month}"
            if kind == "c"
            else f"Сумма просрочки — месяц {month}"
        )
        return label, f"Показатель кредитной истории в месячном срезе {month}."
    readable = normalized.replace("_", " ").strip()
    return readable.capitalize(), "Канонизированный признак клиентской модели риска."


def build_client_feature_definitions(columns: list[str]) -> list[ClientFeatureDefinition]:
    definitions: list[ClientFeatureDefinition] = []
    occupied: dict[str, str] = {}
    for source_name in columns:
        normalized = normalize_field_name(source_name)
        canonical = _ALIAS_TO_CORE.get(normalized) or _fallback_name(source_name)
        if canonical in occupied:
            raise ValueError(
                f"Columns {occupied[canonical]!r} and {source_name!r} resolve to "
                f"the same canonical feature {canonical!r}."
            )
        occupied[canonical] = source_name
        if canonical in _CANONICAL_DEFINITIONS:
            schema_field = _CANONICAL_DEFINITIONS[canonical]
            label, description = schema_field.label, schema_field.description
            aliases = _CORE_ALIASES[canonical]
        else:
            label, description = _fallback_label(source_name)
            aliases = (source_name,)
        definitions.append(
            ClientFeatureDefinition(
                name=canonical,
                aliases=tuple(dict.fromkeys((source_name, canonical, *aliases))),
                label=label,
                description=description,
            )
        )
    return definitions


def canonicalize_client_frame(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, list[str]], dict[str, dict[str, str]]]:
    definitions = build_client_feature_definitions([str(column) for column in frame.columns])
    renamed = frame.copy()
    renamed.columns = [definition.name for definition in definitions]
    sources = {definition.name: list(definition.aliases) for definition in definitions}
    labels = {
        definition.name: {
            "label": definition.label,
            "description": definition.description,
        }
        for definition in definitions
    }
    return renamed, sources, labels


def resolve_client_feature_sources(
    frame: pd.DataFrame,
    feature_columns: list[str],
    feature_sources: dict[str, list[str]],
) -> dict[str, str]:
    by_normalized: dict[str, list[str]] = {}
    for column in frame.columns:
        by_normalized.setdefault(normalize_field_name(str(column)), []).append(str(column))

    resolved: dict[str, str] = {}
    for feature in feature_columns:
        candidates = (feature, *feature_sources.get(feature, (feature,)))
        for candidate in candidates:
            exact = str(candidate)
            if exact in frame.columns:
                resolved[feature] = exact
                break
            matches = by_normalized.get(normalize_field_name(exact), [])
            if len(matches) == 1:
                resolved[feature] = matches[0]
                break
    return resolved
