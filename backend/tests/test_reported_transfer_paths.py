"""Reported Amina/Daniyar amounts must preserve risk and voluntary protection paths."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.domain.models import Card, Transaction
from app.services import fraud_detector, transfer_approval_service


def login(client, code):
    response = client.post('/api/auth/login', json={'test_iin': code, 'password': f'Aman-Test-{code[-4:]}!'})
    assert response.status_code == 200, response.text


def post(client, recipient, amount, **extra):
    return client.post('/api/transfers', json={'recipient': recipient, 'recipient_name': 'server resolves',
        'amount': str(amount), **extra}, headers={'Idempotency-Key': str(uuid4())})


def money(client):
    with client.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance).order_by(Card.id))),
                list(db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id))))


def test_reported_sequence_and_high_risk_gate(client, monkeypatch):
    now = datetime(2026, 10, 15, 12, 14, tzinfo=timezone.utc)
    class FixedTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(fraud_detector, 'datetime', FixedTime)
    monkeypatch.setattr(transfer_approval_service, 'utcnow', lambda: now)
    login(client, 'TEST0006')
    assert client.put('/api/protection-settings', json={'protection_active': True,
        'confirmation_enabled': True, 'notifications_enabled': True}).status_code == 200
    invitation = client.post('/api/trusted-invitations', json={'trusted_iin': 'TEST0003'}).json()
    with TestClient(app) as relative:
        login(relative, 'TEST0003')
        assert relative.post(f"/api/trusted-invitations/{invitation['id']}/accept").status_code == 200
        # Reproduce the earlier 500k outgoing before the reported 50/100/300k sequence.
        first = post(client, '+77040000066', '500000')
        assert first.status_code == 200 and first.json()['status'] == 'completed'
        with client.test_factory() as db:
            for row in db.scalars(select(Transaction).where(Transaction.id >= first.json()['transaction_id'])):
                row.created_at = now - timedelta(minutes=45)
            db.commit()
        for index, amount in enumerate(('50000', '100000', '300000')):
            preview = client.post('/api/transfers/risk-check', json={'recipient': '+77040000066', 'amount': amount})
            assert preview.status_code == 200, preview.text
            assert preview.json()['family_protection_active'] is True
            assert preview.json()['overall_level'] == 'low'
            response = post(client, '+77040000066', amount)
            assert response.status_code == 200 and response.json()['status'] == 'completed', response.text
            with client.test_factory() as db:
                for row in db.scalars(select(Transaction).where(Transaction.id >= response.json()['transaction_id'])):
                    row.created_at = now - timedelta(minutes=3-index)
                db.commit()
        # Remaining balance is 250k: exactly 80% (200k) must wait for the relative.
        before = money(client)
        high = post(client, '+77040000066', '200000')
        assert high.status_code == 200, high.text
        assert high.json()['risk']['overall_level'] in {'high', 'critical'}
        assert high.json()['status'] == 'pending_approval'
        assert money(client) == before
        assert relative.get('/api/transfers/pending-requests').json()[0]['id'] == high.json()['request_id']


def test_daniyar_400k_requires_warning_then_explicit_ack(client):
    login(client, 'TEST0006')
    # Funding is an ordinary real transfer on the isolated fixture database.
    funding = post(client, '+77040000066', '500000')
    assert funding.status_code == 200 and funding.json()['status'] == 'completed'
    login(client, 'TEST0005')
    before = money(client)
    preview = client.post('/api/transfers/risk-check', json={'recipient': '+77050000077', 'amount': '400000'})
    assert preview.status_code == 200, preview.text
    assert preview.json()['overall_level'] in {'high', 'critical'}
    assert preview.json()['requires_risk_acknowledgement'] is True
    assert post(client, '+77050000077', '400000').status_code == 428
    assert money(client) == before
    result = post(client, '+77050000077', '400000', risk_warning_acknowledged=True)
    assert result.status_code == 200 and result.json()['status'] == 'completed', result.text
    assert len(money(client)[1]) == len(before[1]) + 2
