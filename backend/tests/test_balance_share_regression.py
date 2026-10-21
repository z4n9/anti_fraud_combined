"""Real scoring must retain the balance-share gate for familiar recipients."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.domain.models import Card, Transaction, User


def login(client, code="TEST0006"):
    response = client.post("/api/auth/login", json={
        "test_iin": code, "password": f"Aman-Test-{code[-4:]}!"})
    assert response.status_code == 200, response.text


def money(client):
    with client.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance).order_by(Card.id))),
                list(db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id))))


def prepare(client, protection):
    login(client)
    if protection != "off":
        response = client.put("/api/protection-settings", json={
            "protection_active": True, "confirmation_enabled": True,
            "notifications_enabled": True})
        assert response.status_code == 200, response.text
    if protection == "accepted":
        invite = client.post("/api/trusted-invitations", json={"trusted_iin": "TEST0003"})
        assert invite.status_code == 200, invite.text
        login(client, "TEST0003")
        accepted = client.post(f"/api/trusted-invitations/{invite.json()['id']}/accept")
        assert accepted.status_code == 200, accepted.text
        login(client)
    with client.test_factory() as db:
        db.execute(delete(Transaction))
        sender = db.scalar(select(User).where(User.test_iin == "TEST0006"))
        known = db.scalar(select(User).where(User.test_iin == "TEST0005"))
        card = db.scalar(select(Card).where(Card.user_id == sender.id, Card.currency == "KZT")
                         .order_by(Card.id))
        card.balance = 100_000  # Exactly 1,000 KZT; history is deliberately much older.
        now = datetime.now(timezone.utc)
        for index in range(6):
            db.add(Transaction(user_id=sender.id, card_id=card.id, type="transfer",
                title="Prior familiar transfer", amount=-80_000, recipient=known.phone,
                status="completed", created_at=now - timedelta(days=2, hours=index * 3)))
        db.commit()


def submit(client, recipient, amount, key, acknowledged=False):
    return client.post("/api/transfers", json={"recipient": recipient,
        "recipient_name": "Resolved by server", "amount": amount,
        "risk_warning_acknowledged": acknowledged}, headers={"Idempotency-Key": key})


def assert_posted_once(client, before, recipient, amount_cents):
    after = money(client)
    assert len(after[1]) == len(before[1]) + 2
    with client.test_factory() as db:
        sender = db.scalar(select(User).where(User.test_iin == "TEST0006"))
        target = db.scalar(select(User).where(User.phone == recipient))
        cards = {card.user_id: card for card in db.scalars(select(Card).where(Card.currency == "KZT"))}
        prior = dict(before[0])
        assert cards[sender.id].balance == prior[cards[sender.id].id] - amount_cents
        assert cards[target.id].balance == prior[cards[target.id].id] + amount_cents
        assert sum(dict(after[0]).values()) == sum(prior.values())
    return after


@pytest.mark.parametrize("recipient", ["+77040000066", "+77010000043"], ids=["familiar", "new"])
@pytest.mark.parametrize("amount,cents,high", [
    ("799.99", 79_999, False), ("800.00", 80_000, True), ("800.01", 80_001, True)])
@pytest.mark.parametrize("protection", ["off", "accepted", "no-consent"])
def test_boundary_is_independent_of_familiarity(client, recipient, amount, cents, high, protection):
    prepare(client, protection)
    before = money(client)
    preview = client.post("/api/transfers/risk-check", json={"recipient": recipient, "amount": amount})
    assert preview.status_code == 200, preview.text
    risk = preview.json()
    codes = {factor["code"] for factor in risk["transaction_risk"]["factors"]}
    assert ("balance_share" in codes) is high
    assert (risk["overall_level"] in {"high", "critical"}) is high
    assert risk["data_uncertainty"]["sender_history_count"] == 6
    assert money(client) == before  # Preview never posts money.
    key = str(uuid4())
    result = submit(client, recipient, amount, key)
    if high and protection == "off":
        assert result.status_code == 428, result.text
        assert money(client) == before
        result = submit(client, recipient, amount, key, acknowledged=True)
        assert result.status_code == 200 and result.json()["status"] == "completed", result.text
        posted = assert_posted_once(client, before, recipient, cents)
        assert submit(client, recipient, amount, key, acknowledged=True).json() == result.json()
        assert money(client) == posted
    elif high:
        expected = "pending_approval" if protection == "accepted" else "blocked_no_trusted"
        assert result.status_code == 200 and result.json()["status"] == expected, result.text
        assert money(client) == before
        assert submit(client, recipient, amount, key).json() == result.json()
        assert money(client) == before
        if protection == "accepted":
            request_id = result.json()["request_id"]
            # Sender cannot substitute themselves for their trusted relative.
            assert client.post(f"/api/transfers/requests/{request_id}/approve").status_code == 403
            assert money(client) == before
            login(client, "TEST0003")
            approved = client.post(f"/api/transfers/requests/{request_id}/approve")
            assert approved.status_code == 200 and approved.json()["status"] == "completed", approved.text
            posted = assert_posted_once(client, before, recipient, cents)
            repeated = client.post(f"/api/transfers/requests/{request_id}/approve")
            assert repeated.status_code == 200 and repeated.json()["status"] == "completed"
            assert money(client) == posted
    else:
        assert result.status_code == 200 and result.json()["status"] == "completed", result.text
        posted = assert_posted_once(client, before, recipient, cents)
        assert submit(client, recipient, amount, key).json() == result.json()
        assert money(client) == posted


@pytest.mark.parametrize("protection", ["off", "accepted", "no-consent"])
def test_final_command_rechecks_balance_after_low_preview(client, protection):
    prepare(client, protection)
    recipient, amount = "+77040000066", "799.99"
    preview = client.post("/api/transfers/risk-check", json={"recipient": recipient, "amount": amount})
    assert preview.status_code == 200 and preview.json()["overall_level"] == "low", preview.text
    # Simulate an intervening account balance change, not a mocked risk response.
    with client.test_factory() as db:
        sender = db.scalar(select(User).where(User.test_iin == "TEST0006"))
        card = db.scalar(select(Card).where(Card.user_id == sender.id, Card.currency == "KZT")
                         .order_by(Card.id))
        card.balance = 99_000
        db.commit()
    before = money(client)
    response = submit(client, recipient, amount, str(uuid4()))
    assert money(client) == before
    if protection == "off":
        assert response.status_code == 428, response.text
    else:
        assert response.status_code == 200, response.text
        assert response.json()["status"] == ("pending_approval" if protection == "accepted" else "blocked_no_trusted")
        assert "balance_share" in {f["code"] for f in response.json()["risk"]["transaction_risk"]["factors"]}
