"""Real bundled weights through the bank API, beyond injected model decisions."""
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.demo_credentials import demo_password
from app.domain.models import Card, Transaction, TransferRequest
from app.main import app


def login(actor, code):
    response = actor.post('/api/auth/login', json={'test_iin': code, 'password': demo_password(code)})
    assert response.status_code == 200, response.text


def snapshot(client):
    with client.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance).order_by(Card.id))),
                list(db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id))))


def test_real_model_preview_is_hybrid_and_never_persists(client, monkeypatch):
    monkeypatch.setenv('AMAN_ML_MODE', 'required')
    before = snapshot(client)
    response = client.post('/api/transfers/risk-check', json={'recipient': '+77010000043', 'amount': '100.25'})
    assert response.status_code == 200, response.text
    assert response.json()['engine_mode'] == 'hybrid_ml_rules'
    assert response.json()['ml']['status'] == 'available'
    assert response.json()['ml']['decision'] in {'ALLOW', 'REVIEW', 'HIGH_RISK'}
    assert '"features"' not in response.text and '"score":' not in json.dumps(response.json()['ml'])
    assert snapshot(client) == before
    with client.test_factory() as db:
        assert list(db.scalars(select(TransferRequest))) == []


def test_real_model_command_preserves_single_family_execution_and_private_snapshot(client, monkeypatch):
    monkeypatch.setenv('AMAN_ML_MODE', 'required')
    login(client, 'TEST0006')
    assert client.put('/api/protection-settings', json={'protection_active': True,
        'confirmation_enabled': True, 'notifications_enabled': True}).status_code == 200
    invite = client.post('/api/trusted-invitations', json={'trusted_iin': 'TEST0003'}).json()
    with TestClient(app) as son:
        login(son, 'TEST0003')
        assert son.post(f"/api/trusted-invitations/{invite['id']}/accept").status_code == 200
        before = snapshot(client)
        body = {'recipient': '+77020000044', 'recipient_name': 'Марат', 'amount': '1100000'}
        key = str(uuid4())
        result = client.post('/api/transfers', json=body, headers={'Idempotency-Key': key})
        assert result.status_code == 200, result.text
        assert result.json()['status'] == 'pending_approval'
        assert result.json()['risk']['ml']['status'] == 'available'
        assert snapshot(client) == before
        request_id = result.json()['request_id']
        with client.test_factory() as db:
            audit = json.loads(db.get(TransferRequest, request_id).policy_json)['ml_evaluation']
            assert len(audit['features']) == 21 and 0 <= audit['score'] <= 1
            assert audit['currency'] == audit['model_currency'] == 'KZT'
            assert audit['unit_version'] == 'kzt-native-v1' and audit['checksums']
            assert audit['amount_kzt'] == 1100000 and 'amount_eur' not in audit
            assert audit['rule_decision'] == 'REVIEW'
        approved = son.post(f'/api/transfers/requests/{request_id}/approve')
        assert approved.status_code == 200 and approved.json()['status'] == 'completed', approved.text
        posted = snapshot(client)
        assert len(posted[1]) == len(before[1]) + 2
        assert sum(balance for _, balance in posted[0]) == sum(balance for _, balance in before[0])
        assert client.post('/api/transfers', json=body, headers={'Idempotency-Key': key}).json()['status'] == 'completed'
        assert snapshot(client) == posted
        login(client, 'TEST0099')
        staff = client.get(f'/api/analyst/bank-events/{request_id}').json()['ml_assessment']
        assert staff['model_id'] == 'synsepa_s0_clean_kzt' and staff['score'] == audit['score']
        assert staff['currency'] == 'KZT' and 'features' not in staff and 'demo_kzt_per_eur' not in staff
