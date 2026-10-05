"""Independent checks of the authenticated advisory risk boundary."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.domain.models import Card, Transaction, User


BODY = {"recipient": "+77010000043", "amount": "123.45"}
ENDPOINT = "/api/transfers/risk-check"


def ledger_snapshot(client):
    with client.test_factory() as db:
        return (
            list(db.execute(select(Card.id, Card.balance).order_by(Card.id)).all()),
            list(db.execute(select(Transaction.id, Transaction.amount, Transaction.request_key)
                            .order_by(Transaction.id)).all()),
        )


def test_preview_rejects_identity_time_and_model_injection(client):
    before = ledger_snapshot(client)
    for injected in ({"sender_id": 6}, {"as_of": "2030-01-01T00:00:00Z"},
                     {"artifact_dir": "../FraudBanc/backend/artifacts"},
                     {"model_path": "backend/app/main.py"}, {"data_quality_score": 0}):
        response = client.post(ENDPOINT, json={**BODY, **injected})
        assert response.status_code == 422, response.text
    assert ledger_snapshot(client) == before


def test_preview_auth_origin_and_account_guards(client):
    before = ledger_snapshot(client)
    assert client.post(ENDPOINT, json=BODY, headers={"Origin": "https://attacker.test"}).status_code == 403
    assert client.post(ENDPOINT, json=BODY, headers={"X-Account-ID": "6"}).status_code == 409
    client.post("/api/auth/logout")
    assert client.post(ENDPOINT, json=BODY).status_code == 401
    assert ledger_snapshot(client) == before


def test_repeated_preview_does_not_write_or_raise_velocity(client):
    before = ledger_snapshot(client)
    results = []
    for _ in range(8):
        response = client.post(ENDPOINT, json=BODY)
        assert response.status_code == 200, response.text
        result = response.json()
        result.pop("evaluated_at")
        results.append(result)
    assert all(result == results[0] for result in results)
    assert ledger_snapshot(client) == before
    assert results[0]["advisory"] is True
    assert results[0]["transfer_enforcement"] is False


def test_future_completed_transfers_cannot_change_preview(client):
    first = client.post(ENDPOINT, json=BODY)
    assert first.status_code == 200, first.text
    with client.test_factory() as db:
        card = db.scalar(select(Card).where(Card.user_id == 1))
        for index in range(15):
            db.add(Transaction(user_id=1, card_id=card.id, type="transfer", title="Future",
                               amount=-1_000_000_00, recipient=BODY["recipient"],
                               message="private-future-event", status="completed",
                               created_at=datetime.now(timezone.utc) + timedelta(days=1, seconds=index)))
        db.commit()
    before = ledger_snapshot(client)
    second = client.post(ENDPOINT, json=BODY)
    assert second.status_code == 200, second.text
    first_result, second_result = first.json(), second.json()
    first_result.pop("evaluated_at")
    second_result.pop("evaluated_at")
    assert second_result == first_result
    assert ledger_snapshot(client) == before


def test_cycle_explanations_exclude_third_party_identifiers(client):
    with client.test_factory() as db:
        recipient = db.scalar(select(User).where(User.test_iin == "TEST0002"))
        unrelated = db.scalar(select(User).where(User.test_iin == "TEST0005"))
        recipient_card = db.scalar(select(Card).where(Card.user_id == recipient.id))
        unrelated_card = db.scalar(select(Card).where(Card.user_id == unrelated.id))
        now = datetime.now(timezone.utc)
        db.add(Transaction(user_id=recipient.id, card_id=recipient_card.id, type="transfer",
                           title="Private unrelated person", amount=-10000, recipient=unrelated.phone,
                           message="secret-investigation-message", status="completed",
                           created_at=now - timedelta(minutes=2)))
        db.add(Transaction(user_id=unrelated.id, card_id=unrelated_card.id, type="transfer",
                           title="Private sender", amount=-10000, recipient="+77000000025",
                           message="secret-investigation-message", status="completed",
                           created_at=now - timedelta(minutes=1)))
        db.commit()
        secrets = (unrelated.phone, unrelated.test_iin, unrelated.name,
                   "secret-investigation-message", "Private unrelated person")
    response = client.post(ENDPOINT, json=BODY)
    assert response.status_code == 200, response.text
    assert any(factor["code"] == "short_cycle"
               for category in ("recipient_risk", "transaction_risk")
               for factor in response.json()[category]["factors"])
    for secret in secrets:
        assert secret not in response.text
    for category in ("recipient_risk", "transaction_risk"):
        for factor in response.json()[category]["factors"]:
            assert set(factor) == {"code", "label", "description", "strength", "source"}
