"""Bank-to-rule adapter tests using a deterministic server decision time."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from fastapi import HTTPException

from app.domain.models import Card, Transaction, User
from app.services.fraud_detector import FraudDetector

NOW = datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc)  # Noon UTC+5.


def clean(client):
    with client.test_factory() as db:
        db.execute(delete(Transaction))
        db.commit()


def add_row(db, *, user_id=1, cents=-10000, minutes=10, kind="transfer", status="completed",
            recipient="+77010000043", request_key=None, card_id=None):
    card = db.scalar(select(Card).where(Card.user_id == user_id, Card.currency == "KZT").order_by(Card.id))
    row = Transaction(user_id=user_id, card_id=card_id or card.id, type=kind, title="private",
                      amount=cents, recipient=recipient, message="private", status=status,
                      created_at=NOW - timedelta(minutes=minutes), request_key=request_key)
    db.add(row)
    db.flush()
    return row


def preview(client, amount="100", recipient="+77010000043"):
    with client.test_factory() as db:
        return FraudDetector().evaluate_transfer(db, db.get(User, 1), recipient, Decimal(amount), as_of=NOW)


def codes(result):
    return {f.code for component in (result.recipient_risk, result.transaction_risk) for f in component.factors}


def test_known_history_large_new_recipient(client):
    clean(client)
    with client.test_factory() as db:
        for minute in range(20, 26):
            add_row(db, minutes=minute)
        db.commit()
    result = preview(client, "100000", "+77040000066")
    assert {"large_amount", "new_recipient"} <= codes(result)
    assert result.overall_level in {"high", "critical"}
    assert result.data_uncertainty.sender_history_count == 6
    assert result.advisory and not result.transfer_enforcement


@pytest.mark.parametrize("status,minutes", [("pending", 1), ("failed", 1), ("completed", -1), ("completed", 0)])
def test_only_strictly_earlier_completed_rows_count(client, status, minutes):
    clean(client)
    before = preview(client).model_dump()
    with client.test_factory() as db:
        for _ in range(15):
            add_row(db, minutes=minutes, status=status)
        db.commit()
    assert preview(client).model_dump() == before


def test_cold_start_is_uncertain_without_invented_baseline(client):
    clean(client)
    result = preview(client, "100000")
    assert result.data_uncertainty.level == "high"
    assert result.data_uncertainty.sender_history_count == 0
    assert "large_amount" not in codes(result)
    assert "new_recipient" not in codes(result)
    assert result.overall_level == "low"
    result = preview(client, "2500000")
    assert "balance_share" in codes(result)
    assert result.overall_level == "high"


def test_incoming_cashout_does_not_pollute_outgoing_baseline(client):
    clean(client)
    with client.test_factory() as db:
        for minute in range(20, 25):
            add_row(db, minutes=minute, cents=-10000)
        add_row(db, minutes=1, cents=10000000, kind="income", recipient=None)
        db.commit()
    result = preview(client, "100000")
    assert {"rapid_cashout", "large_amount"} <= codes(result)
    assert result.data_uncertainty.sender_history_count == 5


def test_mirrored_income_is_not_a_reverse_graph_edge(client):
    clean(client)
    with client.test_factory() as db:
        recipient = db.scalar(select(User).where(User.test_iin == "TEST0002"))
        outgoing = add_row(db, minutes=2)
        add_row(db, user_id=recipient.id, minutes=2, cents=10000, kind="income",
                recipient="+77000000025", request_key=f"credit:{outgoing.id}")
        db.commit()
    # Repeating A->B must not fabricate B->A and thus a cycle.
    assert "short_cycle" not in codes(preview(client))


def test_many_to_one_remains_visible_for_an_existing_sender(client):
    clean(client)
    with client.test_factory() as db:
        target = db.scalar(select(User).where(User.test_iin == "TEST0002"))
        add_row(db, minutes=1)
        for index in range(9):
            user = User(name="private", phone=f"+7799999{index:04d}")
            db.add(user)
            db.flush()
            card = Card(user_id=user.id, card_name="KZT", last_four=f"{index:04d}",
                        balance=10000, currency="KZT", expiry="09/29", status="active")
            db.add(card)
            db.flush()
            debit = add_row(db, user_id=user.id, minutes=2, recipient=target.phone)
            add_row(db, user_id=target.id, minutes=2, kind="income", cents=10000,
                    request_key=f"credit:{debit.id}")
        db.commit()
    result = preview(client)
    assert "many_to_one" in codes(result)
    assert result.recipient_risk.level == "critical"


@pytest.mark.parametrize("recipient,status", [("+77000000025", 400), ("+77999999999", 404), ("bad", 422)])
def test_invalid_recipient_is_rejected_without_mutation(client, recipient, status):
    clean(client)
    with pytest.raises(HTTPException) as error:
        preview(client, recipient=recipient)
    assert error.value.status_code == status
    with client.test_factory() as db:
        assert list(db.scalars(select(Transaction))) == []


def test_selected_kzt_card_excludes_other_card_history(client):
    clean(client)
    with client.test_factory() as db:
        other = Card(user_id=1, card_name="USD", last_four="9999", balance=1000000,
                     currency="USD", expiry="09/29", status="active")
        db.add(other)
        db.flush()
        for minute in range(1, 10):
            add_row(db, card_id=other.id, minutes=minute)
        db.commit()
    assert preview(client).data_uncertainty.sender_history_count == 0


def test_concurrent_previews_are_stable_without_ledger_writes(client):
    clean(client)
    before = preview(client).model_dump()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: preview(client).model_dump(), range(12)))
    assert all(result == before for result in results)
    with client.test_factory() as db:
        assert list(db.scalars(select(Transaction))) == []


def test_night_signal_uses_local_time(client):
    clean(client)
    with client.test_factory() as db:
        for minute in range(20, 25):
            add_row(db, minutes=minute)
        db.commit()
        detector = FraudDetector()
        # UTC 22:00 is local 03:00; UTC02:00 is local07:00.
        night = detector.evaluate_transfer(db, db.get(User, 1), "+77010000043", Decimal("100"),
                                          as_of=NOW.replace(hour=22))
        day = detector.evaluate_transfer(db, db.get(User, 1), "+77010000043", Decimal("100"), as_of=NOW)
    assert "night_activity" in codes(night)
    assert "night_activity" not in codes(day)
