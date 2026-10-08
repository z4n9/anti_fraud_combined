from __future__ import annotations

import json
import re
import tempfile
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.risk_ledger.domain.canonical_schema import CANONICAL_SCHEMA_V1
from app.risk_ledger.domain.contracts import PhysicalDataType


DictionaryStatus = Literal["candidate", "trusted", "rejected"]
PROMOTION_SUCCESSES = 3
DICTIONARY_SCHEMA_VERSION = "1.0"


def normalize_field_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = re.sub(r"([a-zа-я])([A-ZА-Я])", r"\1_\2", text)
    text = text.casefold().replace("ё", "е")
    text = re.sub(r"[^\w]+", "_", text, flags=re.UNICODE)
    return re.sub(r"_+", "_", text).strip("_")


def _safe_alias(raw_alias: str, normalized: str) -> bool:
    if not normalized or len(normalized) > 128:
        return False
    if "@" in raw_alias or re.search(r"\d{7,}", raw_alias):
        return False
    if len(normalized.split("_")) > 12:
        return False
    return re.fullmatch(r"[\w]+(?:_[\w]+)*", normalized, re.UNICODE) is not None


@dataclass(slots=True)
class DictionaryEntry:
    alias: str
    canonical_field: str
    physical_types: list[str]
    transformations: list[str]
    status: DictionaryStatus
    successes: int
    rejections: int
    source: str
    last_verified_at: str

    @classmethod
    def from_dict(cls, payload: dict) -> "DictionaryEntry":
        return cls(
            alias=str(payload["alias"]),
            canonical_field=str(payload["canonical_field"]),
            physical_types=[str(item) for item in payload.get("physical_types", [])],
            transformations=[
                str(item) for item in payload.get("transformations", [])
            ],
            status=payload.get("status", "candidate"),
            successes=int(payload.get("successes", 0)),
            rejections=int(payload.get("rejections", 0)),
            source=str(payload.get("source", "local_feedback")),
            last_verified_at=str(payload.get("last_verified_at", "")),
        )


class SemanticDictionary:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.version = 1
        self.entries: dict[tuple[str, str], DictionaryEntry] = {}
        if self.path is not None and self.path.exists():
            self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Semantic dictionary cannot be read safely.") from error
        if payload.get("schema_version") != DICTIONARY_SCHEMA_VERSION:
            raise ValueError("Unsupported semantic dictionary schema version.")
        self.version = int(payload.get("version", 1))
        for raw_entry in payload.get("entries", []):
            entry = DictionaryEntry.from_dict(raw_entry)
            if not _safe_alias(entry.alias, entry.alias):
                raise ValueError("Semantic dictionary contains an unsafe alias.")
            if entry.canonical_field not in CANONICAL_SCHEMA_V1.field_map():
                raise ValueError("Semantic dictionary contains an unknown canonical field.")
            if entry.status not in {"candidate", "trusted", "rejected"}:
                raise ValueError("Semantic dictionary contains an invalid status.")
            self.entries[(entry.alias, entry.canonical_field)] = entry

    @property
    def version_label(self) -> str:
        return f"local-{self.version}"

    def trusted_targets(self, alias: str) -> tuple[str, ...]:
        normalized = normalize_field_name(alias)
        return tuple(
            entry.canonical_field
            for entry in self.entries.values()
            if entry.alias == normalized and entry.status == "trusted"
        )

    def entry(self, alias: str, canonical_field: str) -> DictionaryEntry | None:
        return self.entries.get((normalize_field_name(alias), canonical_field))

    def record_confirmation(
        self,
        *,
        alias: str,
        canonical_field: str,
        physical_type: PhysicalDataType,
        transformations: tuple[str, ...] = (),
        accepted: bool,
        source: str = "confirmed_mapping",
    ) -> DictionaryEntry:
        normalized = normalize_field_name(alias)
        if not _safe_alias(alias, normalized):
            raise ValueError("Only safe field aliases can be stored in the dictionary.")
        if canonical_field not in CANONICAL_SCHEMA_V1.field_map():
            raise ValueError("Unknown canonical field cannot be stored.")
        key = (normalized, canonical_field)
        entry = self.entries.get(key)
        if entry is None:
            entry = DictionaryEntry(
                alias=normalized,
                canonical_field=canonical_field,
                physical_types=[],
                transformations=list(transformations),
                status="candidate",
                successes=0,
                rejections=0,
                source=source,
                last_verified_at="",
            )
            self.entries[key] = entry
        type_name = physical_type.value
        if type_name not in entry.physical_types:
            entry.physical_types.append(type_name)
            entry.physical_types.sort()
        entry.last_verified_at = datetime.now(UTC).isoformat()
        if accepted:
            entry.successes += 1
            if (
                entry.rejections == 0
                and entry.successes >= PROMOTION_SUCCESSES
                and len(entry.physical_types) == 1
            ):
                entry.status = "trusted"
            elif len(entry.physical_types) > 1:
                entry.status = "candidate"
            elif entry.status == "rejected":
                entry.status = "candidate"
        else:
            entry.rejections += 1
            entry.status = "rejected"
        self.version += 1
        self._save()
        return entry

    def rollback(self, alias: str, canonical_field: str) -> DictionaryEntry:
        entry = self.entry(alias, canonical_field)
        if entry is None:
            raise KeyError("Dictionary entry was not found.")
        entry.status = "candidate"
        entry.successes = 0
        entry.rejections = 0
        entry.last_verified_at = datetime.now(UTC).isoformat()
        self.version += 1
        self._save()
        return entry

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": DICTIONARY_SCHEMA_VERSION,
            "version": self.version,
            "entries": [
                asdict(entry)
                for entry in sorted(
                    self.entries.values(),
                    key=lambda item: (item.alias, item.canonical_field),
                )
            ],
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
        temporary.replace(self.path)
