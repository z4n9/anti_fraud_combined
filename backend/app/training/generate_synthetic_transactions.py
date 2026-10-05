from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np


GENERATOR_VERSION = "1.0.0"
MANIFEST_SCHEMA_VERSION = "1.0"
DEFAULT_SEED = 20260928
DEFAULT_NORMAL_ROWS = 12_000
DEFAULT_CLIENTS = 240
DEFAULT_DAYS = 120
DEFAULT_START = datetime(2026, 1, 1, tzinfo=UTC)
CSV_FILENAME = "transactions.csv"
MANIFEST_FILENAME = "manifest.json"

FIELDS = (
    "transaction_id",
    "client_id",
    "sender_account_id",
    "recipient_account_id",
    "transaction_timestamp",
    "transaction_amount",
    "currency",
    "channel",
    "device_id",
    "country",
    "direction",
    "balance_before",
    "is_new_recipient",
    "scenario",
    "is_anomaly",
    "synthetic_only",
)

COLUMN_SCHEMA: dict[str, dict[str, Any]] = {
    "transaction_id": {"canonical_field": "transaction.record_id", "type": "string"},
    "client_id": {"canonical_field": "client.record_id", "type": "string"},
    "sender_account_id": {"canonical_field": "account.sender_id", "type": "string"},
    "recipient_account_id": {"canonical_field": "account.recipient_id", "type": "string"},
    "transaction_timestamp": {"canonical_field": "transaction.timestamp", "type": "datetime"},
    "transaction_amount": {"canonical_field": "transaction.amount", "type": "number"},
    "currency": {"canonical_field": "transaction.currency", "type": "category"},
    "channel": {"canonical_field": "transaction.channel", "type": "category"},
    "device_id": {"canonical_field": "device.record_id", "type": "string"},
    "country": {"canonical_field": "location.country", "type": "category"},
    "direction": {"canonical_field": "transaction.direction", "type": "category"},
    "balance_before": {"canonical_field": "account.balance_before", "type": "number"},
    "is_new_recipient": {
        "canonical_field": "transaction.is_new_recipient",
        "type": "boolean",
    },
    "scenario": {"type": "category", "control_only": True},
    "is_anomaly": {"type": "boolean", "control_only": True},
    "synthetic_only": {"type": "boolean", "control_only": True},
}

SCENARIOS: dict[str, dict[str, Any]] = {
    "large_amount": {
        "label": "Необычно крупная сумма",
        "expected_signals": ["amount_outlier", "client_amount_deviation"],
    },
    "frequency_spike": {
        "label": "Резкий рост суточной частоты",
        "expected_signals": ["daily_velocity", "client_frequency_deviation"],
    },
    "rapid_series": {
        "label": "Серия переводов за 5 минут",
        "expected_signals": ["five_minute_velocity", "one_hour_velocity"],
    },
    "structuring": {
        "label": "Дробление общей суммы",
        "expected_signals": ["structuring", "recipient_concentration"],
    },
    "new_recipient": {
        "label": "Новый получатель",
        "expected_signals": ["new_recipient", "recipient_novelty"],
    },
    "night_activity": {
        "label": "Ночная операция",
        "expected_signals": ["unusual_hour", "client_time_deviation"],
    },
    "many_to_one": {
        "label": "Множество отправителей одному получателю",
        "expected_signals": ["many_to_one", "recipient_in_degree"],
    },
    "cycle": {
        "label": "Циклические переводы",
        "expected_signals": ["short_cycle", "graph_risk"],
    },
    "rapid_cashout": {
        "label": "Быстрый вывод поступивших средств",
        "expected_signals": ["rapid_cashout", "inbound_outbound_ratio"],
    },
    "dormant_reactivation": {
        "label": "Активность после длительного бездействия",
        "expected_signals": ["dormant_account", "reactivation"],
    },
    "behavior_deviation": {
        "label": "Несоответствие обычному поведению",
        "expected_signals": [
            "new_device",
            "unusual_country",
            "unusual_currency",
            "client_amount_deviation",
        ],
    },
}

_FORBIDDEN_FIELD_TOKENS = {
    "full_name",
    "first_name",
    "last_name",
    "email",
    "phone",
    "iin",
    "passport",
    "card_number",
    "address",
}
_EMAIL_PATTERN = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_LONG_DIGIT_PATTERN = re.compile(r"(?<!\d)\d{11,19}(?!\d)")


def _client_id(index: int) -> str:
    return f"SYN-CL-{index:06d}"


def _account_id(index: int) -> str:
    return f"SYN-AC-{index:06d}"


def _recipient_id(index: int) -> str:
    return f"SYN-RC-{index:06d}"


def _device_id(index: int) -> str:
    return f"SYN-DV-{index:04d}"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _row(
    *,
    client: str,
    sender: str,
    recipient: str,
    timestamp: datetime,
    amount: float,
    device: str,
    scenario: str = "normal",
    currency: str = "KZT",
    channel: str = "mobile",
    country: str = "KZ",
    direction: str = "outbound",
    balance_before: float = 0.0,
    is_new_recipient: bool = False,
) -> dict[str, Any]:
    return {
        "transaction_id": "",
        "client_id": client,
        "sender_account_id": sender,
        "recipient_account_id": recipient,
        "transaction_timestamp": _iso(timestamp),
        "transaction_amount": round(max(float(amount), 1.0), 2),
        "currency": currency,
        "channel": channel,
        "device_id": device,
        "country": country,
        "direction": direction,
        "balance_before": round(max(float(balance_before), 0.0), 2),
        "is_new_recipient": int(is_new_recipient),
        "scenario": scenario,
        "is_anomaly": int(scenario != "normal"),
        "synthetic_only": 1,
    }


def _normal_rows(
    rng: np.random.Generator,
    *,
    count: int,
    clients: int,
    days: int,
    start: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    profiles: list[dict[str, Any]] = []
    for index in range(1, clients + 1):
        median_amount = float(rng.lognormal(mean=math.log(35_000), sigma=0.65))
        preferred_hour = int(rng.integers(8, 21))
        usual_recipients = [
            _recipient_id((index * 7 + offset) % (clients * 3) + 1)
            for offset in range(3)
        ]
        profiles.append(
            {
                "client": _client_id(index),
                "account": _account_id(index),
                "device": _device_id(index),
                "median_amount": median_amount,
                "preferred_hour": preferred_hour,
                "recipients": usual_recipients,
                "balance": median_amount * float(rng.uniform(8, 30)),
            }
        )

    rows: list[dict[str, Any]] = []
    for _ in range(count):
        profile = profiles[int(rng.integers(0, clients - 1))]
        day = int(rng.integers(0, days))
        hour = int(np.clip(rng.normal(profile["preferred_hour"], 2.2), 6, 23))
        timestamp = start + timedelta(
            days=day,
            hours=hour,
            minutes=int(rng.integers(0, 60)),
            seconds=int(rng.integers(0, 60)),
        )
        amount = float(profile["median_amount"] * rng.lognormal(0, 0.48))
        incoming = bool(rng.random() < 0.16)
        recipient = str(profile["recipients"][int(rng.integers(0, 3))])
        sender = str(profile["account"])
        direction = "outbound"
        if incoming:
            sender, recipient = recipient, sender
            direction = "inbound"
        rows.append(
            _row(
                client=str(profile["client"]),
                sender=sender,
                recipient=recipient,
                timestamp=timestamp,
                amount=amount,
                device=str(profile["device"]),
                channel=("mobile", "web", "atm")[int(rng.choice(3, p=[0.74, 0.18, 0.08]))],
                direction=direction,
                balance_before=float(profile["balance"]),
            )
        )
    return rows, profiles


def _inject_scenarios(
    rows: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    *,
    start: datetime,
    days: int,
) -> None:
    base = start + timedelta(days=days - 5)

    large = profiles[0]
    rows.append(
        _row(
            client=large["client"], sender=large["account"], recipient=_recipient_id(9001),
            timestamp=base + timedelta(hours=10), amount=large["median_amount"] * 45,
            device=large["device"], scenario="large_amount", balance_before=large["balance"] * 4,
            is_new_recipient=True,
        )
    )

    frequency = profiles[1]
    for offset in range(24):
        rows.append(
            _row(
                client=frequency["client"], sender=frequency["account"],
                recipient=frequency["recipients"][offset % 3],
                timestamp=base + timedelta(days=1, hours=8, minutes=offset * 27),
                amount=frequency["median_amount"] * (0.7 + (offset % 4) * 0.1),
                device=frequency["device"], scenario="frequency_spike",
                balance_before=frequency["balance"],
            )
        )

    rapid = profiles[2]
    for offset in range(12):
        rows.append(
            _row(
                client=rapid["client"], sender=rapid["account"],
                recipient=rapid["recipients"][offset % 3],
                timestamp=base + timedelta(days=2, hours=11, seconds=offset * 20),
                amount=rapid["median_amount"] * 0.8, device=rapid["device"],
                scenario="rapid_series", balance_before=rapid["balance"],
            )
        )

    structuring = profiles[3]
    for offset in range(10):
        rows.append(
            _row(
                client=structuring["client"], sender=structuring["account"],
                recipient=_recipient_id(9002),
                timestamp=base + timedelta(days=2, hours=14, minutes=offset * 2),
                amount=94_000 + (offset % 3) * 1_750, device=structuring["device"],
                scenario="structuring", balance_before=1_800_000,
                is_new_recipient=offset == 0,
            )
        )

    new_recipient = profiles[4]
    rows.append(
        _row(
            client=new_recipient["client"], sender=new_recipient["account"],
            recipient=_recipient_id(9003), timestamp=base + timedelta(days=1, hours=16),
            amount=new_recipient["median_amount"] * 3, device=new_recipient["device"],
            scenario="new_recipient", balance_before=new_recipient["balance"],
            is_new_recipient=True,
        )
    )

    night = profiles[5]
    rows.append(
        _row(
            client=night["client"], sender=night["account"], recipient=night["recipients"][0],
            timestamp=base + timedelta(days=3, hours=2, minutes=17),
            amount=night["median_amount"] * 2.2, device=night["device"],
            scenario="night_activity", balance_before=night["balance"],
        )
    )

    mule_recipient = _recipient_id(9004)
    for offset, profile in enumerate(profiles[10:22]):
        rows.append(
            _row(
                client=profile["client"], sender=profile["account"], recipient=mule_recipient,
                timestamp=base + timedelta(days=3, hours=13, minutes=offset * 2),
                amount=35_000 + offset * 2_500, device=profile["device"],
                scenario="many_to_one", balance_before=profile["balance"],
                is_new_recipient=True,
            )
        )

    cycle_profiles = profiles[22:25]
    for offset, profile in enumerate(cycle_profiles):
        recipient_profile = cycle_profiles[(offset + 1) % len(cycle_profiles)]
        rows.append(
            _row(
                client=profile["client"], sender=profile["account"],
                recipient=recipient_profile["account"],
                timestamp=base + timedelta(days=3, hours=17, minutes=offset * 4),
                amount=210_000 - offset * 5_000, device=profile["device"], scenario="cycle",
                balance_before=profile["balance"], is_new_recipient=True,
            )
        )

    cashout = profiles[25]
    inbound_amount = 1_200_000.0
    rows.extend(
        [
            _row(
                client=cashout["client"], sender=_recipient_id(9005), recipient=cashout["account"],
                timestamp=base + timedelta(days=4, hours=9), amount=inbound_amount,
                device=cashout["device"], scenario="rapid_cashout", direction="inbound",
                balance_before=25_000, is_new_recipient=True,
            ),
            _row(
                client=cashout["client"], sender=cashout["account"], recipient=_recipient_id(9006),
                timestamp=base + timedelta(days=4, hours=9, minutes=2), amount=inbound_amount * 0.96,
                device=cashout["device"], scenario="rapid_cashout", direction="outbound",
                balance_before=inbound_amount + 25_000, is_new_recipient=True,
            ),
        ]
    )

    dormant = profiles[-1]
    rows.append(
        _row(
            client=dormant["client"], sender=dormant["account"], recipient=_recipient_id(9007),
            timestamp=base + timedelta(days=4, hours=18), amount=dormant["median_amount"] * 18,
            device=dormant["device"], scenario="dormant_reactivation",
            balance_before=dormant["balance"] * 2, is_new_recipient=True,
        )
    )

    deviation = profiles[26]
    rows.append(
        _row(
            client=deviation["client"], sender=deviation["account"], recipient=_recipient_id(9008),
            timestamp=base + timedelta(days=4, hours=3, minutes=40),
            amount=deviation["median_amount"] * 12, device=_device_id(9001),
            scenario="behavior_deviation", currency="EUR", channel="web", country="DE",
            balance_before=deviation["balance"] * 3, is_new_recipient=True,
        )
    )


def build_synthetic_transactions(
    *,
    seed: int = DEFAULT_SEED,
    normal_rows: int = DEFAULT_NORMAL_ROWS,
    clients: int = DEFAULT_CLIENTS,
    days: int = DEFAULT_DAYS,
    start: datetime = DEFAULT_START,
) -> list[dict[str, Any]]:
    if normal_rows < 1_000:
        raise ValueError("normal_rows must be at least 1000.")
    if clients < 40:
        raise ValueError("clients must be at least 40.")
    if days < 90:
        raise ValueError("days must be at least 90 for historical controls.")
    if start.tzinfo is None:
        raise ValueError("start must be timezone-aware.")

    rng = np.random.default_rng(seed)
    rows, profiles = _normal_rows(
        rng,
        count=normal_rows,
        clients=clients,
        days=days,
        start=start,
    )
    _inject_scenarios(rows, profiles, start=start, days=days)
    rows.sort(
        key=lambda item: (
            item["transaction_timestamp"],
            item["client_id"],
            item["scenario"],
            item["recipient_account_id"],
        )
    )
    for index, item in enumerate(rows, start=1):
        item["transaction_id"] = f"SYN-TX-{index:08d}"
    assert_synthetic_privacy(rows)
    return rows


def assert_synthetic_privacy(rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("Synthetic dataset is empty.")
    forbidden_fields = {
        field for field in rows[0] if field.casefold() in _FORBIDDEN_FIELD_TOKENS
    }
    if forbidden_fields:
        raise ValueError(f"Synthetic dataset contains forbidden fields: {sorted(forbidden_fields)}")
    for row in rows:
        if row.get("synthetic_only") != 1:
            raise ValueError("Every synthetic row must be explicitly marked synthetic_only=1.")
        for value in row.values():
            if not isinstance(value, str):
                continue
            if _EMAIL_PATTERN.search(value) or _LONG_DIGIT_PATTERN.search(value):
                raise ValueError("Synthetic dataset contains a personal-data-like value.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_synthetic_dataset(
    output_dir: str | Path,
    *,
    seed: int = DEFAULT_SEED,
    normal_rows: int = DEFAULT_NORMAL_ROWS,
    clients: int = DEFAULT_CLIENTS,
    days: int = DEFAULT_DAYS,
    start: datetime = DEFAULT_START,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows = build_synthetic_transactions(
        seed=seed,
        normal_rows=normal_rows,
        clients=clients,
        days=days,
        start=start,
    )
    csv_path = output / CSV_FILENAME
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    scenario_counts = Counter(str(row["scenario"]) for row in rows)
    controls = {
        scenario: [
            str(row["transaction_id"])
            for row in rows
            if row["scenario"] == scenario
        ]
        for scenario in SCENARIOS
    }
    manifest: dict[str, Any] = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset_id": "risk-ledger-synthetic-transactions",
        "generator_version": GENERATOR_VERSION,
        "canonical_schema_version": "1.0",
        "synthetic_only": True,
        "suitable_for_real_quality_claims": False,
        "purpose": "Разработка и контроль транзакционного профиля; не реальные банковские данные.",
        "seed": seed,
        "period": {
            "start": _iso(start),
            "end": max(str(row["transaction_timestamp"]) for row in rows),
            "days": days,
        },
        "parameters": {
            "normal_rows": normal_rows,
            "clients": clients,
            "days": days,
        },
        "rows": len(rows),
        "normal_rows": scenario_counts["normal"],
        "anomaly_rows": len(rows) - scenario_counts["normal"],
        "scenario_counts": dict(sorted(scenario_counts.items())),
        "scenarios": {
            name: {
                **definition,
                "control_transaction_ids": controls[name],
            }
            for name, definition in SCENARIOS.items()
        },
        "columns": list(FIELDS),
        "column_schema": COLUMN_SCHEMA,
        "privacy": {
            "contains_real_personal_data": False,
            "identifiers": "Детерминированные идентификаторы с префиксом SYN-.",
            "forbidden_personal_fields_checked": sorted(_FORBIDDEN_FIELD_TOKENS),
        },
        "csv": {
            "filename": CSV_FILENAME,
            "encoding": "utf-8",
            "delimiter": ",",
            "sha256": _sha256(csv_path),
        },
        "reproduce": (
            "python -m app.training.generate_synthetic_transactions "
            f"--output-dir ../data/synthetic --seed {seed} "
            f"--normal-rows {normal_rows} --clients {clients} --days {days}"
        ),
    }
    (output / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the deterministic synthetic transaction fixture."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--normal-rows", type=int, default=DEFAULT_NORMAL_ROWS)
    parser.add_argument("--clients", type=int, default=DEFAULT_CLIENTS)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    return parser


def main() -> None:
    args = _parser().parse_args()
    manifest = generate_synthetic_dataset(
        args.output_dir,
        seed=args.seed,
        normal_rows=args.normal_rows,
        clients=args.clients,
        days=args.days,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
