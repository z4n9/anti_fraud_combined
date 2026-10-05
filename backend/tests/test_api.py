from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from app import seed
from app.core.database import Base, configure_sqlite, get_db
from app.main import app
from app.domain.models import Transaction

pytestmark = pytest.mark.usefixtures("low_transfer_risk")

BODY = {"recipient": "+77010000043", "recipient_name": "Айдана С.", "amount": 50000, "message": "На подарок"}

def test_seed_static_and_privacy(client):
    assert client.get('/').status_code == 200
    assert client.get('/app.js').status_code == 200
    assert client.get('/style.css').status_code == 200
    assert client.get('/backend/aman_bank.db').status_code == 404
    assert client.get('/api/user').json() == {"id": 1, "name": "Алихан", "phone": "+7 700 *** ** 25", "test_iin": "TEST0001"}
    card = client.get('/api/cards').json()[0]
    assert card['balance'] == 2850000
    assert card['last_four'] == '4582'
    assert 'number' not in card
    before = client.get('/api/transactions').json()
    assert len(before) == 4
    assert [t['created_at'] for t in before] == sorted((t['created_at'] for t in before), reverse=True)
    seed.seed_data()
    assert client.get('/api/transactions').json() == before


def test_transfer_persistence_and_message(client):
    response = client.post('/api/transfers', json=BODY, headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['success'] and result['new_balance'] == 2800000
    assert client.get('/api/cards').json()[0]['balance'] == 2800000
    history = client.get('/api/transactions').json()
    assert len(history) == 5
    assert history[0]['id'] == result['transaction_id']
    assert history[0]['amount'] == -50000
    assert history[0]['message'] == 'На подарок'
    # Simulate a fresh application lifespan against the same SQLite file.
    with TestClient(app) as reopened:
        reopened.cookies.update(client.cookies)
        assert reopened.get('/api/cards').json()[0]['balance'] == 2800000
        assert len(reopened.get('/api/transactions').json()) == 5

@pytest.mark.parametrize('amount', [0, -1, 'NaN', 'Infinity', '0.001', 'abc', None, 1000000000000])
def test_invalid_amounts_do_not_mutate(client, amount):
    response = client.post('/api/transfers', json={**BODY, 'amount': amount}, headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == 422, response.text
    assert isinstance(response.json()['detail'], str)
    assert client.get('/api/cards').json()[0]['balance'] == 2850000
    assert len(client.get('/api/transactions').json()) == 4


def test_overdraft_and_empty_recipient(client):
    assert client.post('/api/transfers', json={**BODY, 'amount': 2850001}, headers={'Idempotency-Key': str(uuid4())}).status_code == 400
    assert client.post('/api/transfers', json={**BODY, 'recipient': ' '}, headers={'Idempotency-Key': str(uuid4())}).status_code == 422
    assert client.post('/api/transfers', json={**BODY, 'recipient_name': ''}, headers={'Idempotency-Key': str(uuid4())}).status_code == 422
    assert client.get('/api/cards').json()[0]['balance'] == 2850000


def test_fractional_amount_is_exact(client):
    for _ in range(3):
        assert client.post('/api/transfers', json={**BODY, 'amount': '0.10'}, headers={'Idempotency-Key': str(uuid4())}).status_code == 200
    assert Decimal(str(client.get('/api/cards').json()[0]['balance'])) == Decimal('2849999.70')


def test_idempotent_retry(client):
    headers = {'Idempotency-Key': 'same-transfer'}
    first = client.post('/api/transfers', json=BODY, headers=headers)
    again = client.post('/api/transfers', json=BODY, headers=headers)
    assert first.json() == again.json()
    assert len(client.get('/api/transactions').json()) == 5
    assert client.post('/api/transfers', json={**BODY, 'amount': 1}, headers=headers).status_code == 409


def test_parallel_transfers_cannot_overdraw(client):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post('/api/transfers', json={**BODY, 'amount': 2000000}, headers={'Idempotency-Key': str(uuid4())}), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 400]
    assert client.get('/api/cards').json()[0]['balance'] == 850000
    assert len(client.get('/api/transactions').json()) == 5


def test_parallel_duplicate_full_balance(client):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post('/api/transfers', json={**BODY, 'amount': 2850000}, headers={'Idempotency-Key': 'full-balance'}), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert client.get('/api/cards').json()[0]['balance'] == 0
    assert len(client.get('/api/transactions').json()) == 5


def test_rollback_if_history_write_fails(client):
    def fail_insert(*args):
        raise RuntimeError('test: transaction insert failed')
    event.listen(Transaction, 'before_insert', fail_insert)
    try:
        with pytest.raises(RuntimeError):
            client.post('/api/transfers', json=BODY, headers={'Idempotency-Key': str(uuid4())})
    finally:
        event.remove(Transaction, 'before_insert', fail_insert)
    assert client.get('/api/cards').json()[0]['balance'] == 2850000
    assert len(client.get('/api/transactions').json()) == 4


def test_trusted_person_requires_new_workflow(client):
    person = client.get('/api/trusted-person').json()
    assert person['verified'] is False
    assert person['family_protection_ready'] is False
    payload = {'name': 'Тестовый родственник', 'phone': '+77020000044', 'relationship': 'Брат'}
    assert client.put('/api/trusted-person', json=payload).status_code == 409
    settings = dict(protection_active=True, notifications_enabled=False, confirmation_enabled=False)
    assert client.put('/api/protection-settings', json=settings).status_code == 200
    with TestClient(app) as reopened:
        reopened.cookies.update(client.cookies)
        saved = reopened.get('/api/trusted-person').json()
        assert saved['protection_active'] is True
        assert saved['family_protection_ready'] is False
    assert client.put('/api/protection-settings', json={}).status_code == 422
