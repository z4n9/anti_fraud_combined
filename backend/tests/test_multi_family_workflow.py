"""Real-risk quorum, delayed removal and legacy migration regressions."""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.main import app
from app.domain.models import Card, Transaction, TransferParticipant, TransferRequest, TrustedPerson, utcnow
from app.migrations import migrate

BODY = {'recipient': '+77010000043', 'recipient_name': 'ignored', 'amount': '1000000.25'}
SETTINGS = {'protection_active': True, 'confirmation_enabled': True, 'notifications_enabled': True}


def login(client, code):
    assert client.post('/api/auth/login', json={'test_iin': code, 'password': f'Aman-Test-{code[-4:]}!'}).status_code == 200


@pytest.fixture
def relatives(client):
    login(client, 'TEST0006')
    assert client.put('/api/protection-settings', json=SETTINGS).status_code == 200
    with TestClient(app) as son, TestClient(app) as daughter:
        ids = []
        for actor, code in [(son, 'TEST0003'), (daughter, 'TEST0007')]:
            login(actor, code)
            invite = client.post('/api/trusted-invitations', json={'trusted_iin': code})
            assert invite.status_code == 200, invite.text
            ids.append(invite.json()['id'])
            assert actor.post(f'/api/trusted-invitations/{ids[-1]}/accept').status_code == 200
        yield client, son, daughter, ids


def money(client):
    with client.test_factory() as db:
        return (db.execute(select(Card.id, Card.balance).order_by(Card.id)).all(),
                db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id)).all())


@pytest.mark.parametrize('answers,first,last,expected', [
    (None, 'approve', 'approve', 'completed'),
    (None, 'reject', 'reject', 'rejected'),
    (None, 'approve', 'reject', 'bank_review'),
    ({'pressure': True, 'secrecy': None, 'stranger': False}, 'approve', 'approve', 'bank_review'),
    ({'pressure': True}, 'reject', 'reject', 'rejected'),
])
def test_real_risk_waits_for_every_frozen_vote(relatives, answers, first, last, expected):
    owner, son, daughter, _ = relatives
    before = money(owner)
    body = dict(BODY)
    if answers is not None:
        body['anti_scam'] = answers
    command = owner.post('/api/transfers', json=body, headers={'Idempotency-Key': str(uuid4())})
    assert command.status_code == 200, command.text
    row = command.json()
    assert row['status'] == 'pending_approval'
    path = f"/api/transfers/requests/{row['request_id']}"
    one = daughter.post(f'{path}/{first}')
    assert one.status_code == 200 and one.json()['status'] == 'pending_approval'
    assert money(owner) == before
    assert len(one.json()['participants']) == 2
    two = son.post(f'{path}/{last}')
    assert two.status_code == 200, two.text
    assert two.json()['status'] == expected
    replay = daughter.post(f'{path}/{first}')
    assert replay.status_code == 200 and replay.json()['status'] == expected
    assert replay.json()['my_vote'] == first
    if expected != 'completed':
        assert money(owner) == before
    else:
        with owner.test_factory() as db:
            saved = db.get(TransferRequest, row['request_id'])
            assert db.get(Card, saved.sender_card_id).balance == 19999975
    unknown = two.json()['anti_scam']
    if answers is None:
        assert unknown == {'pressure': None, 'secrecy': None, 'stranger': None}


def test_targeted_removal_retains_other_consent_and_blocks_old_quorum(relatives):
    owner, son, _, ids = relatives
    row = owner.post('/api/transfers', json=BODY, headers={'Idempotency-Key': str(uuid4())}).json()
    assert owner.post('/api/protection-change-requests', json={'action': 'remove'}).status_code == 422
    change = owner.post('/api/protection-change-requests', json={'action': 'remove', 'invitation_id': ids[0]}).json()
    with owner.test_factory() as db:
        db.execute(text('UPDATE protection_change_requests SET effective_at=:due WHERE id=:id'),
                   {'due': utcnow() - timedelta(seconds=1), 'id': change['id']})
        db.commit()
    before = money(owner)
    assert son.post(f"/api/transfers/requests/{row['request_id']}/approve").status_code == 403
    assert money(owner) == before
    person = owner.get('/api/trusted-person').json()
    assert person['family_protection_ready'] and person['current_invitation_id'] == ids[1]


def test_legacy_pending_rebuild_preserves_money_and_disabled_assignment(relatives):
    owner, son, _, ids = relatives
    completed = owner.post('/api/transfers', json={**BODY, 'amount': '1'}, headers={'Idempotency-Key': str(uuid4())}).json()
    assert completed['status'] == 'completed'
    row = owner.post('/api/transfers', json=BODY, headers={'Idempotency-Key': str(uuid4())}).json()
    before = money(owner)
    with owner.test_factory() as db:
        engine = db.get_bind()
        db.execute(text('DELETE FROM transfer_participants'))
        person = db.scalar(select(TrustedPerson).where(TrustedPerson.current_invitation_id == ids[0]))
        person.protection_active = False
        db.commit()
    with engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        ddl = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='transfer_requests'").scalar()
        indexes = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='transfer_requests' AND sql IS NOT NULL").scalars().all()
        ddl = ddl.replace('CREATE TABLE transfer_requests', 'CREATE TABLE legacy_requests').replace("'bank_review',", '').replace(",'bank_review'", '')
        assert "'bank_review'" not in ddl
        assert 'bank_review_reason' in ddl
        connection.exec_driver_sql(ddl)
        connection.exec_driver_sql('INSERT INTO legacy_requests SELECT * FROM transfer_requests')
        connection.exec_driver_sql('DROP TABLE transfer_requests')
        connection.exec_driver_sql('ALTER TABLE legacy_requests RENAME TO transfer_requests')
        for statement in indexes:
            connection.exec_driver_sql(statement)
        connection.commit()
        connection.exec_driver_sql('PRAGMA foreign_keys=ON')
    migrate(engine)
    migrate(engine)
    assert money(owner) == before
    with owner.test_factory() as db:
        participants = list(db.scalars(select(TransferParticipant).where(TransferParticipant.request_id == row['request_id'])))
        assert len(participants) == 1 and participants[0].invitation_id == ids[0]
        assert db.execute(text('PRAGMA foreign_key_check')).all() == []
        assert db.get(TransferRequest, row['request_id']).amount_cents == 100000025
        assert db.get(TransferRequest, completed['request_id']).status == 'completed'
    assert son.post(f"/api/transfers/requests/{row['request_id']}/approve").status_code == 403
    assert owner.put('/api/protection-settings', json=SETTINGS).status_code == 200
    restored = son.post(f"/api/transfers/requests/{row['request_id']}/approve")
    assert restored.status_code == 200 and restored.json()['status'] == 'completed'
