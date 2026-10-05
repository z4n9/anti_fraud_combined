from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select, event
from app.domain.models import Card, Transaction, User

pytestmark = pytest.mark.usefixtures("low_transfer_risk")

BODY = {'recipient': '+77020000044', 'recipient_name': 'Марат Омаров', 'amount': '123.45', 'message': 'Проверка'}

def recipient_card(client):
    with client.test_factory() as db:
        user = db.scalar(select(User).where(User.test_iin == 'TEST0003'))
        return db.scalar(select(Card).where(Card.user_id == user.id))

def test_credit_and_duplicate(client):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post('/api/transfers', json=BODY, headers={'Idempotency-Key':'credit-test'}), range(2)))
    assert [r.status_code for r in responses] == [200,200]
    assert responses[0].json() == responses[1].json()
    assert client.get('/api/cards').json()[0]['balance'] == 2849876.55
    assert recipient_card(client).balance == 12345
    client.post('/api/auth/login', json={'test_iin':'TEST0003','password':'Aman-Test-0003!'})
    history = client.get('/api/transactions').json()
    assert len(history) == 1
    assert history[0]['type'] == 'income'
    assert history[0]['amount'] == 123.45
    assert history[0]['title'] == 'Алихан'
    assert history[0]['message'] == 'Проверка'
    assert client.get('/api/cards').json()[0]['balance'] == 123.45

def test_credit_history_failure_rolls_back_both_accounts(client):
    def fail_income(mapper, connection, target):
        if target.type == 'income':
            raise RuntimeError('income insert failed')
    event.listen(Transaction, 'before_insert', fail_income)
    try:
        with pytest.raises(RuntimeError):
            client.post('/api/transfers', json=BODY, headers={'Idempotency-Key': str(uuid4())})
    finally:
        event.remove(Transaction, 'before_insert', fail_income)
    assert recipient_card(client).balance == 0
    assert client.get('/api/cards').json()[0]['balance'] == 2850000
    assert len(client.get('/api/transactions').json()) == 4

def test_inactive_recipient_and_self_transfer(client):
    card = recipient_card(client)
    with client.test_factory() as db:
        db.get(Card, card.id).status = 'blocked'
        db.commit()
    assert client.post('/api/transfers', json=BODY, headers={'Idempotency-Key': str(uuid4())}).status_code == 400
    assert client.post('/api/transfers', json={**BODY,'recipient':'+77000000025'}, headers={'Idempotency-Key': str(uuid4())}).status_code == 400
    assert client.get('/api/cards').json()[0]['balance'] == 2850000
    assert recipient_card(client).balance == 0
