"""Independent authorization and expiry checks for delayed protection changes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.domain.models import Card, Transaction, TrustedInvitation, TrustedPerson
from app.main import app

BASE = "/api/protection-change-requests"
TRANSFER = {"recipient": "+77010000043", "recipient_name": "Айдана",
            "amount": "1000000.25", "message": "Expiry security"}
SETTINGS = {"protection_active": True, "notifications_enabled": True, "confirmation_enabled": True}


@contextmanager
def extra_client():
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()


def login(client, code):
    response = client.post("/api/auth/login", json={"test_iin": code,
        "password": f"Aman-Test-{code[-4:]}!"})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def family(client):
    owner = login(client, "TEST0006")
    assert client.put("/api/protection-settings", json=SETTINGS).status_code == 200
    invitation = client.post("/api/trusted-invitations", json={"trusted_iin": "TEST0003"})
    assert invitation.status_code == 200, invitation.text
    # Reuse the fixture lifespan; extra clients must not restart analyst runtime.
    with extra_client() as son, extra_client() as stranger:
        login(son, "TEST0003")
        login(stranger, "TEST0005")
        assert son.post(f"/api/trusted-invitations/{invitation.json()['id']}/accept").status_code == 200
        yield client, son, stranger, owner["id"], invitation.json()["id"]


def create(client, action):
    response = client.post(BASE, json={"action": action})
    assert response.status_code == 200, response.text
    return response.json()


def snapshot(client):
    with client.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance).order_by(Card.id)).all()),
                list(db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id)).all()))


def due(client, request_id):
    from app.domain.models import ProtectionChangeRequest
    with client.test_factory() as db:
        row = db.get(ProtectionChangeRequest, request_id)
        row.effective_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()


def stored(client, request_id):
    from app.domain.models import ProtectionChangeRequest
    with client.test_factory() as db:
        row = db.get(ProtectionChangeRequest, request_id)
        return row.status


@pytest.mark.parametrize("action", ["disable", "remove"])
def test_foreign_owner_cannot_cancel_or_see_requests(family, action):
    owner, son, stranger, _, _ = family
    before = snapshot(owner)
    row = create(owner, action)
    for actor in (son, stranger):
        assert actor.get(BASE).json() == []
        response = actor.post(f"{BASE}/{row['id']}/cancel")
        assert response.status_code == 404, response.text
    assert stored(owner, row["id"]) == "pending"
    assert snapshot(owner) == before


def test_guest_and_analyst_cannot_change_protection(client):
    with extra_client() as guest:
        for method, path, body in [("get", BASE, None), ("post", BASE, {"action": "disable"}),
                                   ("post", f"{BASE}/1/cancel", {})]:
            response = getattr(guest, method)(path, **({"json": body} if body is not None else {}))
            assert response.status_code == 401
    login(client, "TEST0099")
    assert client.get(BASE).status_code == 403
    assert client.post(BASE, json={"action": "disable"}).status_code == 403
    assert client.post(f"{BASE}/1/cancel").status_code == 403


@pytest.mark.parametrize("extra", ["effective_at", "status", "owner_user_id", "invitation_id", "resolved_at"])
def test_server_deadline_identity_and_status_cannot_be_supplied(family, extra):
    owner, _, _, _, _ = family
    response = owner.post(BASE, json={"action": "disable", extra: "2000-01-01T00:00:00Z"})
    assert response.status_code == 422, response.text
    assert owner.get(BASE).json() == []


@pytest.mark.parametrize("action", ["disable", "remove"])
def test_pending_change_retains_accepted_family_and_server_24h_deadline(family, action):
    owner, son, _, owner_id, invitation_id = family
    row = create(owner, action)
    created_at = datetime.fromisoformat(row["created_at"])
    effective_at = datetime.fromisoformat(row["effective_at"])
    assert effective_at - created_at == timedelta(hours=24)
    with owner.test_factory() as db:
        person = db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == owner_id))
        invite = db.get(TrustedInvitation, invitation_id)
        assert person.protection_active and person.current_invitation_id == invitation_id
        assert person.invitation_status == invite.status == "accepted"
        assert invite.accepted_by_user_id == invite.recipient_user_id
    transfer = owner.post("/api/transfers", json=TRANSFER, headers={"Idempotency-Key": str(uuid4())})
    assert transfer.status_code == 200, transfer.text
    assert transfer.json()["status"] == "pending_approval"
    assert son.get("/api/transfers/pending-requests").json()[0]["id"] == transfer.json()["request_id"]


def test_replacement_and_confirmation_toggle_cannot_bypass_accepted_consent(family):
    owner, _, _, _, invitation_id = family
    assert owner.post("/api/trusted-invitations", json={"trusted_iin": "TEST0007"}).status_code == 409
    assert owner.put("/api/protection-settings", json={**SETTINGS, "confirmation_enabled": False}).status_code == 409
    assert owner.put("/api/protection-settings", json={**SETTINGS, "notifications_enabled": False}).status_code == 200
    person = owner.get("/api/trusted-person").json()
    assert person["current_invitation_id"] == invitation_id
    assert person["confirmation_enabled"] and person["protection_active"]
    assert person["notifications_enabled"] is False


@pytest.mark.parametrize("action", ["disable", "remove"])
def test_expiry_survives_failed_relative_approval_without_money(family, action):
    owner, son, _, _, _ = family
    before = snapshot(owner)
    pending = owner.post("/api/transfers", json=TRANSFER, headers={"Idempotency-Key": str(uuid4())})
    assert pending.status_code == 200 and pending.json()["status"] == "pending_approval"
    row = create(owner, action)
    due(owner, row["id"])
    # No intervening GET may apply the deadline on this test's behalf.
    response = son.post(f"/api/transfers/requests/{pending.json()['request_id']}/approve")
    assert response.status_code == 403, response.text
    assert stored(owner, row["id"]) == "executed"
    assert snapshot(owner) == before


@pytest.mark.parametrize("trigger", ["create_transfer", "relative_queue", "owner_queue"])
def test_due_disable_is_applied_by_transfer_commands_and_queues(family, trigger):
    owner, son, _, owner_id, _ = family
    before = snapshot(owner)
    row = create(owner, "disable")
    due(owner, row["id"])
    if trigger == "create_transfer":
        response = owner.post("/api/transfers", json=TRANSFER, headers={"Idempotency-Key": str(uuid4())})
        assert response.status_code == 200 and response.json()["status"] == "blocked_no_trusted"
    elif trigger == "relative_queue":
        assert son.get("/api/transfers/pending-requests").status_code == 200
    else:
        assert owner.get("/api/transfers/requests").status_code == 200
    assert stored(owner, row["id"]) == "executed"
    with owner.test_factory() as db:
        assert db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == owner_id)).protection_active is False
    assert snapshot(owner) == before


def test_concurrent_duplicates_create_one_pending_change(family):
    owner, _, _, _, _ = family
    with extra_client() as parallel:
        parallel.cookies.update(owner.cookies)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(actor.post, BASE, json={"action": "disable"}) for actor in (owner, parallel)]
            responses = [future.result() for future in futures]
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert len(owner.get(BASE).json()) == 1


def test_cancel_payload_is_strict_and_retries_preserve_accepted_family(family):
    owner, _, _, owner_id, invitation_id = family
    before = snapshot(owner)
    row = create(owner, "remove")
    path = f"{BASE}/{row['id']}/cancel"
    assert owner.post(path, json={"status": "executed"}).status_code == 422
    assert stored(owner, row["id"]) == "pending"
    first = owner.post(path, json={})
    second = owner.post(path)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "cancelled"
    with owner.test_factory() as db:
        person = db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == owner_id))
        assert person.protection_active and person.current_invitation_id == invitation_id
        assert db.get(TrustedInvitation, invitation_id).status == "accepted"
    assert snapshot(owner) == before


def test_cancel_cannot_resurrect_executed_due_change(family):
    owner, _, _, owner_id, _ = family
    row = create(owner, "disable")
    due(owner, row["id"])
    with extra_client() as parallel:
        parallel.cookies.update(owner.cookies)
        with ThreadPoolExecutor(max_workers=2) as pool:
            cancel = pool.submit(owner.post, f"{BASE}/{row['id']}/cancel")
            queue = pool.submit(parallel.get, "/api/transfers/requests")
            assert cancel.result().status_code == 409
            assert queue.result().status_code == 200
    assert stored(owner, row["id"]) == "executed"
    with owner.test_factory() as db:
        assert db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == owner_id)).protection_active is False


def test_exact_deadline_changes_state_and_primitive_does_not_commit(family):
    from app.domain.models import ProtectionChangeRequest
    from app.services.protection_changes import apply_due
    owner, _, _, owner_id, _ = family
    row = create(owner, "disable")
    boundary = datetime.fromisoformat(row["effective_at"])
    with owner.test_factory() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        assert apply_due(db, owner_id, now=boundary - timedelta(microseconds=1)) is False
        assert db.get(ProtectionChangeRequest, row["id"]).status == "pending"
        assert apply_due(db, owner_id, now=boundary) is True
        assert db.get(ProtectionChangeRequest, row["id"]).status == "executed"
        assert db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == owner_id)).protection_active is False
        db.rollback()
    # The primitive belongs to its caller's transaction, never commits implicitly.
    assert stored(owner, row["id"]) == "pending"


def test_deadline_crossing_after_preflight_remains_committed_on_403(family, monkeypatch):
    from app.services import protection_changes
    owner, son, _, _, _ = family
    before = snapshot(owner)
    pending = owner.post("/api/transfers", json=TRANSFER, headers={"Idempotency-Key": str(uuid4())})
    assert pending.status_code == 200 and pending.json()["status"] == "pending_approval"
    row = create(owner, "disable")
    boundary = datetime.fromisoformat(row["effective_at"])
    original = protection_changes.settle_due

    def deadline_crosses_after_preflight(db, user_id=None):
        original(db, user_id)
        # Preflight saw a future deadline; decision lock must recheck and persist
        # the now-effective lifecycle before a denied financial action rolls back.
        monkeypatch.setattr(protection_changes, "utcnow", lambda: boundary)

    monkeypatch.setattr(protection_changes, "settle_due", deadline_crosses_after_preflight)
    response = son.post(f"/api/transfers/requests/{pending.json()['request_id']}/approve")
    assert response.status_code == 403, response.text
    assert stored(owner, row["id"]) == "executed"
    assert snapshot(owner) == before
