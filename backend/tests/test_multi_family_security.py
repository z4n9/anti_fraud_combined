"""Independent checks of frozen family consensus and employee resolution."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text

from app.domain.models import Card, MockCitizen, MockRelationship, Transaction, TransferRequest, TrustedInvitation, User
from app.main import app

TRANSFERS = "/api/transfers"
BANK = "/api/analyst/bank-events"
BODY = {"recipient": "+77010000043", "recipient_name": "Айдана", "amount": "1000000.25",
        "message": "Frozen family security"}
SETTINGS = {"protection_active": True, "notifications_enabled": True, "confirmation_enabled": True}


@contextmanager
def account(code=None):
    client = TestClient(app)
    try:
        if code:
            login(client, code)
        yield client
    finally:
        client.close()


def login(client, code):
    response = client.post("/api/auth/login", json={"test_iin": code,
        "password": f"Aman-Test-{code[-4:]}!"})
    assert response.status_code == 200, response.text
    return response.json()


def snapshot(client):
    with client.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance).order_by(Card.id)).all()),
                list(db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id)).all()))


def invite(owner, relative, code):
    response = owner.post("/api/trusted-invitations", json={"trusted_iin": code})
    assert response.status_code == 200, response.text
    invitation_id = response.json()["id"]
    accepted = relative.post(f"/api/trusted-invitations/{invitation_id}/accept")
    assert accepted.status_code == 200, accepted.text
    return invitation_id


@pytest.fixture
def family(client):
    owner = login(client, "TEST0006")
    assert client.put("/api/protection-settings", json=SETTINGS).status_code == 200
    with account("TEST0003") as son, account("TEST0007") as daughter, account("TEST0099") as employee:
        son_id = invite(client, son, "TEST0003")
        daughter_id = invite(client, daughter, "TEST0007")
        yield client, son, daughter, employee, owner["id"], son_id, daughter_id


def create(owner, body=None, key=None):
    response = owner.post(TRANSFERS, json=body or BODY, headers={"Idempotency-Key": key or str(uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


def vote(actor, request_id, decision):
    return actor.post(f"{TRANSFERS}/requests/{request_id}/{decision}")


def bank(actor, request_id, decision, **extra):
    return actor.post(f"{BANK}/{request_id}/{decision}", json={"note": "Проверены обстоятельства перевода", **extra})


def add_verified_relationship(client, code):
    # Fixture-only eGov data for testing the three-relative bound.
    with client.test_factory() as db:
        owner = db.scalar(select(MockCitizen).where(MockCitizen.test_iin == "TEST0006"))
        relative = db.scalar(select(MockCitizen).where(MockCitizen.test_iin == code))
        db.add(MockRelationship(person_1_id=owner.id, person_2_id=relative.id,
            relationship_from_1_to_2="дочь", relationship_from_2_to_1="мать"))
        db.commit()


def test_first_vote_does_not_spend_and_sender_cannot_vote(family):
    owner, son, daughter, _, _, _, _ = family
    before = snapshot(owner)
    row = create(owner)
    request_id = row["request_id"]
    assert row["status"] == "pending_approval"
    assert vote(owner, request_id, "approve").status_code == 403
    first = vote(son, request_id, "approve")
    assert first.status_code == 200 and first.json()["status"] == "pending_approval"
    assert snapshot(owner) == before
    second = vote(daughter, request_id, "approve")
    assert second.status_code == 200 and second.json()["status"] == "completed"
    after = snapshot(owner)
    assert len(after[1]) == len(before[1]) + 2
    assert vote(son, request_id, "approve").status_code == 200
    assert vote(daughter, request_id, "approve").status_code == 200
    assert snapshot(owner) == after


@pytest.mark.parametrize("second_vote, expected", [("reject", "rejected"), ("approve", "bank_review")])
def test_all_responses_needed_for_rejection_or_conflict(family, second_vote, expected):
    owner, son, daughter, _, _, _, _ = family
    before = snapshot(owner)
    request_id = create(owner)["request_id"]
    response = vote(son, request_id, "reject")
    assert response.status_code == 200 and response.json()["status"] == "pending_approval"
    assert vote(son, request_id, "approve").status_code == 409
    final = vote(daughter, request_id, second_vote)
    assert final.status_code == 200 and final.json()["status"] == expected
    assert snapshot(owner) == before


def test_new_relative_cannot_join_existing_frozen_consensus(family):
    owner, son, daughter, _, _, _, _ = family
    request_id = create(owner)["request_id"]
    add_verified_relationship(owner, "TEST0004")
    with account("TEST0004") as third:
        invite(owner, third, "TEST0004")
        assert vote(third, request_id, "approve").status_code == 403
        assert vote(son, request_id, "approve").json()["status"] == "pending_approval"
        result = vote(daughter, request_id, "approve")
        assert result.status_code == 200 and result.json()["status"] == "completed"


def test_verified_bound_and_individual_consent_cannot_be_spoofed(family):
    owner, _, _, _, _, _, _ = family
    add_verified_relationship(owner, "TEST0004")
    add_verified_relationship(owner, "TEST0005")
    pending = owner.post("/api/trusted-invitations", json={"trusted_iin": "TEST0004"})
    assert pending.status_code == 200
    assert owner.post(f"/api/trusted-invitations/{pending.json()['id']}/accept").status_code == 403
    assert owner.post("/api/trusted-invitations", json={"trusted_iin": "TEST0005"}).status_code == 409
    assert owner.post("/api/trusted-invitations", json={"trusted_iin": "TEST0001"}).status_code == 400
    before = snapshot(owner)
    request_id = create(owner)["request_id"]
    with account("TEST0004") as unconsenting:
        assert vote(unconsenting, request_id, "approve").status_code == 403
    assert snapshot(owner) == before


def test_removed_voted_relative_is_not_dropped_from_frozen_consensus(family):
    from app.domain.models import ProtectionChangeRequest
    owner, son, daughter, employee, _, son_invitation, _ = family
    before = snapshot(owner)
    request_id = create(owner)["request_id"]
    assert vote(son, request_id, "approve").status_code == 200
    removal = owner.post("/api/protection-change-requests", json={"action": "remove", "invitation_id": son_invitation})
    assert removal.status_code == 200, removal.text
    with owner.test_factory() as db:
        db.get(ProtectionChangeRequest, removal.json()["id"]).effective_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    response = vote(daughter, request_id, "approve")
    assert response.status_code in {403, 409}, response.text
    assert bank(employee, request_id, "approve").status_code == 409
    assert snapshot(owner) == before
    # A new command still includes the remaining relative and retains protection.
    new = create(owner)
    assert new["status"] == "pending_approval"
    assert vote(daughter, new["request_id"], "approve").json()["status"] == "completed"


def test_antiscam_true_cannot_bypass_high_risk_family_or_missing_family(family):
    owner, son, daughter, employee, _, _, _ = family
    before = snapshot(owner)
    row = create(owner, {**BODY, "anti_scam": {"pressure": True}})
    assert row["status"] == "pending_approval"
    assert bank(employee, row["request_id"], "approve").status_code == 409
    assert vote(son, row["request_id"], "approve").json()["status"] == "pending_approval"
    assert vote(daughter, row["request_id"], "approve").json()["status"] == "bank_review"
    assert snapshot(owner) == before
    with account("TEST0001") as unprotected:
        blocked = create(unprotected, {**BODY, "amount": "2500000.25", "anti_scam": {"pressure": True}})
        assert blocked["status"] == "blocked_no_trusted"
        assert bank(employee, blocked["request_id"], "approve").status_code == 409


def test_unknown_answers_are_frozen_and_idempotency_detects_answer_change(family):
    owner, _, _, _, _, _, _ = family
    key = str(uuid4())
    row = create(owner, key=key)
    own = owner.get(f"{TRANSFERS}/requests").json()
    stored = next(item for item in own if item["id"] == row["request_id"])
    assert stored["anti_scam"] == {"pressure": None, "secrecy": None, "stranger": None}
    changed = owner.post(TRANSFERS, json={**BODY, "anti_scam": {"pressure": False}}, headers={"Idempotency-Key": key})
    assert changed.status_code == 409
    same = owner.post(TRANSFERS, json={**BODY, "anti_scam": {}}, headers={"Idempotency-Key": key})
    assert same.status_code == 200 and same.json()["request_id"] == row["request_id"]
    assert owner.post(TRANSFERS, json={**BODY, "anti_scam": {"decision": "ALLOW"}}, headers={"Idempotency-Key": str(uuid4())}).status_code == 422


@pytest.mark.parametrize("value", ["false", "true", 0, 1])
def test_antiscam_answers_do_not_coerce_strings_or_numbers(family, value):
    owner, _, _, _, _, _, _ = family
    before = snapshot(owner)
    response = owner.post(TRANSFERS, json={**BODY, "anti_scam": {"pressure": value}},
                          headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 422, response.text
    assert snapshot(owner) == before


def test_low_risk_pressure_routes_to_employee_without_granting_bank_access(family):
    owner, _, _, employee, _, _, _ = family
    before = snapshot(owner)
    row = create(owner, {**BODY, "amount": "100", "anti_scam": {"secrecy": True}})
    assert row["status"] == "bank_review"
    assert snapshot(owner) == before
    response = bank(employee, row["request_id"], "approve")
    assert response.status_code == 200 and response.json()["status"] == "completed"
    assert len(snapshot(owner)[1]) == len(before[1]) + 2
    assert employee.get("/api/cards").status_code == 403


def conflict(family):
    owner, son, daughter, _, _, _, _ = family
    request_id = create(owner)["request_id"]
    assert vote(son, request_id, "approve").status_code == 200
    result = vote(daughter, request_id, "reject")
    assert result.status_code == 200 and result.json()["status"] == "bank_review"
    return request_id


def test_employee_authorization_note_and_terminal_actions_are_restricted(family):
    owner, son, daughter, employee, _, _, _ = family
    before = snapshot(owner)
    pending = create(owner)["request_id"]
    assert bank(employee, pending, "approve").status_code == 409
    assert bank(employee, pending, "reject").status_code == 409
    request_id = conflict(family)
    with account() as guest:
        assert bank(guest, request_id, "approve").status_code == 401
    for client in (owner, son, daughter):
        assert bank(client, request_id, "approve").status_code == 403
        assert client.get(BANK).status_code == 403
    assert bank(employee, request_id, "approve", actor_user_id=1).status_code == 422
    assert employee.post(f"{BANK}/{request_id}/approve", json={"note": "     "}).status_code == 422
    assert snapshot(owner) == before
    assert bank(employee, request_id, "reject").status_code == 200
    assert bank(employee, request_id, "approve").status_code == 409
    assert snapshot(owner) == before


def test_employee_cannot_release_conflict_after_consent_revocation(family):
    owner, _, _, employee, _, son_invitation, _ = family
    before = snapshot(owner)
    request_id = conflict(family)
    with owner.test_factory() as db:
        db.get(TrustedInvitation, son_invitation).accepted_by_user_id = None
        db.commit()
    response = bank(employee, request_id, "approve")
    assert response.status_code == 409, response.text
    assert snapshot(owner) == before


def test_competing_employee_approve_reject_commits_one_audit_and_result(family):
    from app.domain.models import BankReviewDecision
    owner, _, _, employee, _, _, _ = family
    request_id = conflict(family)
    before = snapshot(owner)
    with account() as competing:
        competing.cookies.update(employee.cookies)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(bank, actor, request_id, decision)
                       for actor, decision in [(employee, "approve"), (competing, "reject")]]
            responses = [future.result() for future in futures]
    assert sorted(response.status_code for response in responses) == [200, 409]
    with owner.test_factory() as db:
        decisions = list(db.scalars(select(BankReviewDecision).where(BankReviewDecision.request_id == request_id)))
        assert len(decisions) == 1
        analyst = db.scalar(select(User).where(User.test_iin == "TEST0099"))
        assert decisions[0].actor_user_id == analyst.id
        winning_action = decisions[0].action
        assert db.get(TransferRequest, request_id).status == ("completed" if winning_action == "approve" else "rejected")
    after = snapshot(owner)
    assert len(after[1]) - len(before[1]) == (2 if winning_action == "approve" else 0)
    assert employee.get("/api/cards").status_code == 403


def test_failed_audit_insert_rolls_back_money_and_terminal_status(family):
    from app.domain.models import BankReviewDecision
    owner, _, _, employee, _, _, _ = family
    request_id = conflict(family)
    before = snapshot(owner)
    engine = owner.test_factory.kw["bind"]

    def audit_failure(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO BANK_REVIEW_DECISIONS"):
            raise RuntimeError("independent audit write failure")

    event.listen(engine, "before_cursor_execute", audit_failure)
    try:
        with pytest.raises(RuntimeError, match="independent audit write failure"):
            bank(employee, request_id, "approve")
    finally:
        event.remove(engine, "before_cursor_execute", audit_failure)
    assert snapshot(owner) == before
    with owner.test_factory() as db:
        assert db.get(TransferRequest, request_id).status == "bank_review"
        assert list(db.scalars(select(BankReviewDecision).where(BankReviewDecision.request_id == request_id))) == []
    assert bank(employee, request_id, "approve").status_code == 200
    assert len(snapshot(owner)[1]) == len(before[1]) + 2


def test_migrated_stale_assignment_keeps_history_without_granting_authority(family):
    from app.domain.models import TransferParticipant
    from app.migrations import migrate
    owner, son, _, employee, _, son_invitation, _ = family
    before = snapshot(owner)
    request_id = create(owner)["request_id"]
    engine = owner.test_factory.kw["bind"]
    # Simulate the legacy single-assignee shape only inside the fixture database.
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM transfer_participants WHERE request_id=:id"), {"id": request_id})
        connection.execute(text("UPDATE trusted_invitations SET active=0 WHERE id=:id"), {"id": son_invitation})
        connection.exec_driver_sql("ALTER TABLE transfer_requests DROP COLUMN anti_scam_json")
        connection.exec_driver_sql("ALTER TABLE transfer_requests DROP COLUMN bank_review_reason")
    migrate(engine)
    migrate(engine)
    with owner.test_factory() as db:
        assigned = list(db.scalars(select(TransferParticipant).where(TransferParticipant.request_id == request_id)))
        assert len(assigned) == 1 and assigned[0].invitation_id == son_invitation
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
    assert vote(son, request_id, "approve").status_code == 403
    assert bank(employee, request_id, "approve").status_code == 409
    assert snapshot(owner) == before


def test_final_family_vote_rolls_back_with_failed_ledger_write(family):
    from app.domain.models import TransferParticipant
    owner, son, daughter, _, _, _, daughter_invitation = family
    request_id = create(owner)["request_id"]
    assert vote(son, request_id, "approve").status_code == 200
    before = snapshot(owner)
    engine = owner.test_factory.kw["bind"]

    def ledger_failure(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO TRANSACTIONS"):
            raise RuntimeError("independent final vote ledger failure")

    event.listen(engine, "before_cursor_execute", ledger_failure)
    try:
        with pytest.raises(RuntimeError, match="independent final vote ledger failure"):
            vote(daughter, request_id, "approve")
    finally:
        event.remove(engine, "before_cursor_execute", ledger_failure)
    assert snapshot(owner) == before
    with owner.test_factory() as db:
        row = db.get(TransferRequest, request_id)
        assert row.status == "pending_approval" and row.transaction_id is None
        last = db.scalar(select(TransferParticipant).where(TransferParticipant.request_id == request_id,
                         TransferParticipant.invitation_id == daughter_invitation))
        assert last.response == "pending" and last.responded_at is None
    assert vote(daughter, request_id, "approve").json()["status"] == "completed"
