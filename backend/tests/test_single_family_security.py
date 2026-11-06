"""Independent security checks for the first eligible family response policy."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.core.demo_credentials import demo_password
from app.domain.models import (
    Card, Transaction, TransferParticipant, TransferRequest, TrustedInvitation, utcnow,
)
from app.main import app


BASE = "/api/transfers"
BODY = {"recipient": "+77010000043", "recipient_name": "Айдана",
        "amount": "1000000.25", "message": "Independent first response"}


def login(actor, code):
    response = actor.post("/api/auth/login", json={"test_iin": code, "password": demo_password(code)})
    assert response.status_code == 200, response.text
    return response.json()


def snapshot(owner):
    with owner.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance).order_by(Card.id)).all()),
                list(db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id)).all()))


@pytest.fixture
def first_family(client):
    owner = login(client, "TEST0006")
    response = client.put("/api/protection-settings", json={"protection_active": True,
        "notifications_enabled": True, "confirmation_enabled": True})
    assert response.status_code == 200, response.text
    actors = [TestClient(app) for _ in range(4)]
    son, daughter, stranger, employee = actors
    try:
        logins = [login(actor, code) for actor, code in zip(actors,
                  ("TEST0003", "TEST0007", "TEST0005", "TEST0099"))]
        invitations = []
        for actor, code in ((son, "TEST0003"), (daughter, "TEST0007")):
            invited = client.post("/api/trusted-invitations", json={"trusted_iin": code})
            assert invited.status_code == 200, invited.text
            invitation_id = invited.json()["id"]
            accepted = actor.post(f"/api/trusted-invitations/{invitation_id}/accept")
            assert accepted.status_code == 200, accepted.text
            invitations.append(invitation_id)
        yield client, son, daughter, stranger, employee, owner, logins, invitations
    finally:
        for actor in actors:
            actor.close()


def create(owner, *, pressure=False):
    key = str(uuid4())
    body = {**BODY, "anti_scam": {"pressure": True}} if pressure else BODY
    response = owner.post(BASE, json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 200 and response.json()["status"] == "pending_approval", response.text
    return response.json()["request_id"], key


def vote(actor, request_id, choice):
    return actor.post(f"{BASE}/requests/{request_id}/{choice}")


def bank(employee, request_id):
    return employee.post(f"/api/analyst/bank-events/{request_id}/approve",
                         json={"note": "Независимо проверены обстоятельства операции"})


def revoke(owner, invitation_id):
    with owner.test_factory() as db:
        db.get(TrustedInvitation, invitation_id).accepted_by_user_id = None
        db.commit()


@pytest.mark.parametrize("choice,status", [("approve", "completed"), ("reject", "rejected")])
def test_only_first_eligible_response_commits_and_other_voter_cannot_change_it(first_family, choice, status):
    owner, son, daughter, stranger, _, _, _, _ = first_family
    before = snapshot(owner)
    request_id, _ = create(owner)
    for actor in (owner, stranger):
        assert vote(actor, request_id, "approve").status_code == 403
        assert vote(actor, request_id, "reject").status_code == 403
    result = vote(son, request_id, choice)
    assert result.status_code == 200 and result.json()["status"] == status, result.text
    after = snapshot(owner)
    assert len(after[1]) - len(before[1]) == (2 if choice == "approve" else 0)
    assert sum(b for _, b in after[0]) == sum(b for _, b in before[0])
    for later_choice in ("approve", "reject"):
        assert vote(daughter, request_id, later_choice).status_code == 409
    assert vote(son, request_id, choice).status_code == 200
    assert vote(son, request_id, "reject" if choice == "approve" else "approve").status_code == 409
    assert snapshot(owner) == after
    with owner.test_factory() as db:
        row = db.get(TransferRequest, request_id)
        people = list(db.scalars(select(TransferParticipant).where(TransferParticipant.request_id == request_id)))
        assert len([person for person in people if person.response != "pending"]) == 1
        assert json.loads(row.policy_json)["family_decision"]["response"] == choice
    assert "new_balance" not in result.text and "sender_card_id" not in result.text


def test_revoked_nonresponding_candidate_does_not_block_valid_first_response(first_family):
    owner, son, daughter, _, _, _, _, invitations = first_family
    request_id, _ = create(owner)
    revoke(owner, invitations[0])
    assert vote(son, request_id, "approve").status_code == 403
    result = vote(daughter, request_id, "approve")
    assert result.status_code == 200 and result.json()["status"] == "completed", result.text


@pytest.mark.parametrize("revoke_decider", [False, True])
def test_antiscam_bank_release_depends_on_frozen_deciding_approver_only(first_family, revoke_decider):
    owner, son, daughter, _, employee, _, _, invitations = first_family
    before = snapshot(owner)
    request_id, _ = create(owner, pressure=True)
    assert bank(employee, request_id).status_code == 409
    first = vote(son, request_id, "approve")
    assert first.status_code == 200 and first.json()["status"] == "bank_review", first.text
    assert snapshot(owner) == before
    assert vote(daughter, request_id, "reject").status_code == 409
    revoke(owner, invitations[0 if revoke_decider else 1])
    result = bank(employee, request_id)
    if revoke_decider:
        assert result.status_code == 409, result.text
        assert snapshot(owner) == before
    else:
        assert result.status_code == 200 and result.json()["status"] == "completed", result.text
        assert len(snapshot(owner)[1]) == len(before[1]) + 2


def test_different_relatives_racing_approve_reject_have_one_durable_winner(first_family):
    owner, son, daughter, _, _, _, _, _ = first_family
    before = snapshot(owner)
    request_id, _ = create(owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(vote, actor, request_id, choice)
                   for actor, choice in ((son, "approve"), (daughter, "reject"))]
        responses = [future.result() for future in futures]
    assert sorted(result.status_code for result in responses) == [200, 409], [r.text for r in responses]
    winner = next(result.json() for result in responses if result.status_code == 200)
    after = snapshot(owner)
    assert winner["status"] in {"completed", "rejected"}
    assert len(after[1]) - len(before[1]) == (2 if winner["status"] == "completed" else 0)
    assert sum(b for _, b in after[0]) == sum(b for _, b in before[0])
    with owner.test_factory() as db:
        people = list(db.scalars(select(TransferParticipant).where(TransferParticipant.request_id == request_id)))
        assert len([person for person in people if person.response != "pending"]) == 1


def test_failed_first_approval_rolls_back_vote_snapshot_and_money(first_family):
    owner, son, daughter, _, _, _, _, _ = first_family
    before = snapshot(owner)
    request_id, _ = create(owner)
    engine = owner.test_factory.kw["bind"]

    def fail_ledger(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO TRANSACTIONS"):
            raise RuntimeError("independent first-response ledger failure")

    event.listen(engine, "before_cursor_execute", fail_ledger)
    try:
        with pytest.raises(RuntimeError, match="independent first-response ledger failure"):
            vote(son, request_id, "approve")
    finally:
        event.remove(engine, "before_cursor_execute", fail_ledger)
    assert snapshot(owner) == before
    with owner.test_factory() as db:
        row = db.get(TransferRequest, request_id)
        assert row.status == "pending_approval" and row.transaction_id is None
        assert not json.loads(row.policy_json).get("family_decision")
        assert all(person.response == "pending" and person.responded_at is None for person in
                   db.scalars(select(TransferParticipant).where(TransferParticipant.request_id == request_id)))
    assert vote(daughter, request_id, "reject").json()["status"] == "rejected"
    assert snapshot(owner) == before


def make_legacy(owner, request_id, *, voted=False, bank_review=False):
    with owner.test_factory() as db:
        row = db.get(TransferRequest, request_id)
        policy = json.loads(row.policy_json)
        policy["policy_version"] = "family-v3"
        policy.pop("required_family_approvals", None)
        policy.pop("family_decision", None)
        row.policy_json = json.dumps(policy)
        people = list(db.scalars(select(TransferParticipant).where(
            TransferParticipant.request_id == request_id).order_by(TransferParticipant.id)))
        if voted:
            people[0].response, people[0].responded_at = "approve", utcnow()
        if bank_review:
            people[1].response, people[1].responded_at = "reject", utcnow()
            row.status, row.bank_review_reason = "bank_review", "conflicting_votes"
        db.commit()


def test_legacy_pending_without_votes_upgrades_without_spending(first_family):
    owner, _, daughter, _, _, _, _, _ = first_family
    before = snapshot(owner)
    request_id, _ = create(owner)
    make_legacy(owner, request_id)
    response = owner.get(f"{BASE}/requests")
    assert response.status_code == 200, response.text
    assert snapshot(owner) == before
    with owner.test_factory() as db:
        row = db.get(TransferRequest, request_id)
        assert row.status == "pending_approval"
        assert json.loads(row.policy_json)["policy_version"] == "family-v4-first-response"
    assert vote(daughter, request_id, "approve").json()["status"] == "completed"


@pytest.mark.parametrize("surface", ["list", "replay", "vote", "bank-list", "bank-decision"])
def test_legacy_pending_with_saved_vote_is_cancelled_durably_without_spending(first_family, surface):
    owner, _, daughter, _, employee, _, _, _ = first_family
    before = snapshot(owner)
    request_id, key = create(owner)
    make_legacy(owner, request_id, voted=True)
    if surface == "list":
        response = owner.get(f"{BASE}/requests")
    elif surface == "replay":
        response = owner.post(BASE, json=BODY, headers={"Idempotency-Key": key})
    elif surface == "vote":
        response = vote(daughter, request_id, "approve")
    elif surface == "bank-list":
        response = employee.get("/api/analyst/bank-events")
    else:
        response = bank(employee, request_id)
    assert response.status_code in {200, 409}, response.text
    assert snapshot(owner) == before
    with owner.test_factory() as db:
        row = db.get(TransferRequest, request_id)
        assert row.status == "cancelled" and row.transaction_id is None
        assert json.loads(row.policy_json).get("resolution_note")
    repeated = bank(employee, request_id)
    assert repeated.status_code in {200, 409}, repeated.text
    if repeated.status_code == 200:
        assert repeated.json()["status"] == "cancelled"
    assert snapshot(owner) == before


def test_legacy_bank_conflict_keeps_original_consensus_authorization(first_family):
    owner, _, _, _, employee, _, _, _ = first_family
    request_id, _ = create(owner)
    make_legacy(owner, request_id, voted=True, bank_review=True)
    before = snapshot(owner)
    response = bank(employee, request_id)
    assert response.status_code == 200 and response.json()["status"] == "completed", response.text
    assert len(snapshot(owner)[1]) == len(before[1]) + 2
    with owner.test_factory() as db:
        assert json.loads(db.get(TransferRequest, request_id).policy_json)["policy_version"] == "family-v3"


def test_expired_first_response_request_never_executes(first_family):
    owner, son, _, _, employee, _, _, _ = first_family
    before = snapshot(owner)
    request_id, _ = create(owner, pressure=True)
    assert vote(son, request_id, "approve").json()["status"] == "bank_review"
    with owner.test_factory() as db:
        db.get(TransferRequest, request_id).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    response = bank(employee, request_id)
    assert response.status_code == 200 and response.json()["status"] == "expired", response.text
    assert snapshot(owner) == before
