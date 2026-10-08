"""Independent security checks of the concrete family approval boundary."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Card, ProtectionChangeRequest, Transaction, TrustedInvitation, TrustedPerson, User, utcnow
from app.main import app

BASE = "/api/transfers"
BODY = {"recipient": "+77010000043", "recipient_name": "Айдана",
        "amount": "1000000.25", "message": "Фиксированное поручение"}


def login(client, code):
    response = client.post("/api/auth/login", json={"test_iin": code,
                           "password": f"Aman-Test-{code[-4:]}!"})
    assert response.status_code == 200, response.text
    return response.json()


def snapshot(client):
    with client.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance).order_by(Card.id)).all()),
                list(db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id)).all()))


@pytest.fixture
def approval_family(client):
    owner = login(client, "TEST0006")
    response = client.put("/api/protection-settings", json={"protection_active": True,
                          "notifications_enabled": True, "confirmation_enabled": True})
    assert response.status_code == 200, response.text
    invitation = client.post("/api/trusted-invitations", json={"trusted_iin": "TEST0003"})
    assert invitation.status_code == 200, invitation.text
    with TestClient(app) as son, TestClient(app) as stranger:
        trusted = login(son, "TEST0003")
        login(stranger, "TEST0005")
        response = son.post(f"/api/trusted-invitations/{invitation.json()['id']}/accept")
        assert response.status_code == 200, response.text
        yield client, son, stranger, owner, trusted


def pending(owner):
    key = str(uuid4())
    response = owner.post(BASE, json=BODY, headers={"Idempotency-Key": key})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending_approval"
    return response.json()["request_id"], key


def action(request_id, verb):
    return f"{BASE}/requests/{request_id}/{verb}"


def test_sender_and_unrelated_user_cannot_decide_or_read_family_queue(approval_family):
    owner, son, stranger, _, _ = approval_family
    before = snapshot(owner)
    request_id, _ = pending(owner)
    for actor in (owner, stranger):
        for verb in ("approve", "reject"):
            response = actor.post(action(request_id, verb))
            assert response.status_code == 403, response.text
    assert stranger.post(action(request_id, "cancel")).status_code == 403
    assert stranger.get(f"{BASE}/pending-requests").json() == []
    assert stranger.get(f"{BASE}/requests").json() == []
    assert snapshot(owner) == before
    assert son.get(f"{BASE}/pending-requests").json()[0]["id"] == request_id


def test_decision_payload_cannot_change_frozen_transfer(approval_family):
    owner, son, _, _, _ = approval_family
    before = snapshot(owner)
    request_id, key = pending(owner)
    for injected in ({"amount": "1"}, {"recipient": "+77040000066"},
                     {"status": "completed"}, {"sender_user_id": 1}, {"risk": {"overall_level": "low"}}):
        response = son.post(action(request_id, "approve"), json=injected)
        assert response.status_code == 422, response.text
    changed = owner.post(BASE, json={**BODY, "amount": "1"}, headers={"Idempotency-Key": key})
    assert changed.status_code == 409
    assert snapshot(owner) == before
    response = son.post(action(request_id, "approve"))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    with owner.test_factory() as db:
        sender = db.scalar(select(User).where(User.test_iin == "TEST0006"))
        target = db.scalar(select(User).where(User.test_iin == "TEST0002"))
        debit = db.scalar(select(Transaction).where(Transaction.user_id == sender.id,
                          Transaction.type == "transfer"))
        credit = db.scalar(select(Transaction).where(Transaction.user_id == target.id,
                           Transaction.type == "income"))
        assert debit.amount == -100000025
        assert credit.amount == 100000025
        assert debit.recipient == BODY["recipient"]
        assert debit.message == credit.message == BODY["message"]


@pytest.mark.parametrize("revocation", ["disable", "replace", "kinship", "consent"])
def test_revoked_or_replaced_family_cannot_execute_existing_request(approval_family, revocation):
    owner, son, _, account, _ = approval_family
    before = snapshot(owner)
    request_id, key = pending(owner)
    if revocation == "disable":
        response = owner.put("/api/protection-settings", json={"protection_active": False,
                             "notifications_enabled": True, "confirmation_enabled": True})
        assert response.status_code == 200
        assert owner.get('/api/trusted-person').json()['protection_active'] is True
        with owner.test_factory() as db:
            change = db.scalar(select(ProtectionChangeRequest).where(ProtectionChangeRequest.status == 'pending'))
            change.effective_at = utcnow() - timedelta(seconds=1)
            db.commit()
    elif revocation == "replace":
        old_invitation_id = owner.get('/api/trusted-person').json()['current_invitation_id']
        response = owner.post("/api/trusted-invitations", json={"trusted_iin": "TEST0007"})
        assert response.status_code == 200, response.text
        assert owner.post('/api/protection-change-requests', json={'action': 'remove', 'invitation_id': old_invitation_id}).status_code == 200
        with owner.test_factory() as db:
            change = db.scalar(select(ProtectionChangeRequest).where(ProtectionChangeRequest.status == 'pending'))
            change.effective_at = utcnow() - timedelta(seconds=1)
            db.commit()
        response = owner.post("/api/trusted-invitations", json={"trusted_iin": "TEST0007"})
        assert response.status_code == 200, response.text
    else:
        with owner.test_factory() as db:
            trusted = db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == account["id"]))
            if revocation == "kinship":
                trusted.relationship_verified = False
            else:
                invitation = db.get(TrustedInvitation, trusted.current_invitation_id)
                invitation.accepted_by_user_id = None
            db.commit()
    response = son.post(action(request_id, "approve"))
    assert response.status_code in {403, 409}, response.text
    replay = owner.post(BASE, json=BODY, headers={"Idempotency-Key": key})
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] != "completed"
    assert snapshot(owner) == before


def test_duplicate_approvals_execute_once_and_relative_response_has_no_balance(approval_family):
    owner, son, _, _, _ = approval_family
    before_cards, before_history = snapshot(owner)
    request_id, _ = pending(owner)
    queue = son.get(f"{BASE}/pending-requests")
    assert queue.status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: son.post(action(request_id, "approve")), range(2)))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    for response in (queue, *responses):
        assert '"balance":' not in response.text.lower()
        assert "new_balance" not in response.text
        assert "balance_after" not in response.text
        assert "sender_card_id" not in response.text
        assert "risk_json" not in response.text
    after_cards, after_history = snapshot(owner)
    assert len(after_history) == len(before_history) + 2
    assert sum(balance for _, balance in after_cards) == sum(balance for _, balance in before_cards)


def test_approve_reject_race_has_one_terminal_decision(approval_family):
    owner, son, _, _, _ = approval_family
    before_cards, before_history = snapshot(owner)
    request_id, _ = pending(owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda verb: son.post(action(request_id, verb)), ("approve", "reject")))
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    result = next(r.json() for r in responses if r.status_code == 200)
    after_cards, after_history = snapshot(owner)
    if result["status"] == "completed":
        assert len(after_history) == len(before_history) + 2
        assert sum(value for _, value in after_cards) == sum(value for _, value in before_cards)
    else:
        assert result["status"] == "rejected"
        assert (after_cards, after_history) == (before_cards, before_history)
    other_verb = "reject" if result["status"] == "completed" else "approve"
    assert son.post(action(request_id, other_verb)).status_code == 409


def test_owner_cancel_prevents_later_approval(approval_family):
    owner, son, _, _, _ = approval_family
    before = snapshot(owner)
    request_id, key = pending(owner)
    assert son.post(action(request_id, "cancel")).status_code == 403
    response = owner.post(action(request_id, "cancel"))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert son.post(action(request_id, "approve")).status_code == 409
    replay = owner.post(BASE, json=BODY, headers={"Idempotency-Key": key})
    assert replay.status_code == 200
    assert replay.json()["status"] == "cancelled"
    assert snapshot(owner) == before
