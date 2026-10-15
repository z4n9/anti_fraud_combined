"""Recipient-specific rolling windows, independent of execution risk policy."""
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from app.domain.models import Card, Transaction, User
from app.main import app
from app.services.recipient_monitoring import detect_recipient_alerts

NOW = datetime(2020, 1, 15, 10, 0, tzinfo=timezone.utc)
URL = '/api/analyst/recipient-monitoring'


def row(index, *, minutes=0, sender=None, recipient='receiver', amount='10000', currency='KZT'):
    return {'id': str(index), 'timestamp': (NOW + timedelta(minutes=minutes)).isoformat(),
            'sender_id': sender or f'sender{index}', 'recipient_id': recipient,
            'amount': amount, 'currency': currency}


def login(client):
    result = client.post('/api/auth/login', json={'test_iin': 'TEST0099', 'password': 'Aman-Test-0099!'})
    assert result.status_code == 200
    return result.json()['id']


def test_exact_threshold_peak_is_order_independent_with_equal_timestamps():
    records = [row(index, minutes=60 if index > 4 else 0, sender=f'S{index % 8}') for index in range(10)]
    expected, _ = detect_recipient_alerts(records)
    assert len(expected) == 1
    assert expected[0]['small_transfer_count'] == 10 and expected[0]['new_senders'] == 8
    assert expected[0]['total_amount'] == 100000
    for seed in range(5):
        shuffled = records.copy()
        Random(seed).shuffle(shuffled)
        assert detect_recipient_alerts(shuffled)[0] == expected
    # A microsecond over the inclusive hour boundary loses five members.
    for record in records[5:]:
        record['timestamp'] = (NOW + timedelta(hours=1, microseconds=1)).isoformat()
    assert detect_recipient_alerts(records)[0] == []


def test_known_history_and_small_transfer_threshold_prevent_false_alerts():
    records = [row(index, minutes=index, sender=f'S{index % 8}') for index in range(10)]
    assert len(detect_recipient_alerts(records)[0]) == 1
    old = [row(f'old{index}', minutes=-120-index*120, sender=f'S{index}', amount='20000') for index in range(8)]
    assert detect_recipient_alerts(records + old)[0] == []
    assert detect_recipient_alerts(records[:9])[0] == []
    assert detect_recipient_alerts([{**record, 'amount': '10000.01'} for record in records])[0] == []


def test_duplicate_ids_currency_selfloops_and_invalid_rows_do_not_inflate_count():
    records = [row(index) for index in range(9)]
    invalid = [row('foreign', currency='EUR'), row('self', sender='receiver'), row('bad', amount='NaN'),
               row('zero', amount=0), {**row('missing'), 'recipient_id': None}]
    assert detect_recipient_alerts(records + records + invalid)[0] == []
    # Conflicting copies of one ID are excluded completely, regardless of order.
    records += [row(9), {**row(9), 'amount': 2}]
    assert detect_recipient_alerts(records)[0] == []
    assert detect_recipient_alerts(list(reversed(records)))[0] == []


def test_demo_has_one_signal_and_legitimate_merchant_counterexample():
    path = Path(__file__).resolve().parents[2] / 'examples' / 'recipient_monitoring_demo.csv'
    with path.open(encoding='utf-8', newline='') as stream:
        source = list(csv.DictReader(stream))
    assert len(source) == 30
    records = [{'id': value['transaction_id'], 'timestamp': value['transaction_timestamp'],
                'sender_id': value['sender_account_id'], 'recipient_id': value['recipient_account_id'],
                'amount': value['transaction_amount'], 'currency': value['currency']} for value in source]
    alerts, _ = detect_recipient_alerts(records)
    assert len(alerts) == 1 and alerts[0]['recipient_id'] == 'RISK_RECEIVER'
    assert alerts[0]['small_transfer_count'] == alerts[0]['new_senders'] == 12


def bank_snapshot(client):
    with client.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance).order_by(Card.id))),
                list(db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id))))


def test_bank_monitoring_counts_only_proven_debits_once_without_money_writes(client):
    with client.test_factory() as db:
        target = db.scalar(select(Card).where(Card.user_id == 1))
        for index in range(10):
            user = User(name=f'Synthetic sender {index}', phone=f'+77800000{index:03d}', role='client')
            db.add(user)
            db.flush()
            card = Card(user_id=user.id, card_name='fixture', last_four=f'{index:04d}',
                        balance=0, currency='KZT', expiry='01/30', status='active')
            db.add(card)
            db.flush()
            stamp = NOW + timedelta(minutes=index)
            debit = Transaction(user_id=user.id, card_id=card.id, type='transfer', title='fixture',
                                amount=-100000, recipient='ignored', status='completed', created_at=stamp)
            db.add(debit)
            db.flush()
            db.add(Transaction(user_id=target.user_id, card_id=target.id, type='income', title='fixture',
                               amount=100000, status='completed', request_key=f'credit:{debit.id}', created_at=stamp))
        db.commit()
        target_id = target.id
    # Time is a fixture constant in the past relative to bank observation horizon.
    before = bank_snapshot(client)
    login(client)
    response = client.get(URL)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['source']['kind'] == 'bank' and payload['source']['truncated'] is False
    assert payload['total'] == 1
    assert payload['items'][0]['recipient_id'] == f'card:{target_id}'
    assert payload['items'][0]['small_transfer_count'] == 10
    assert payload['items'][0]['total_amount'] == 10000
    assert client.get(URL, params={'recipient_id': 'not-an-alert'}).json()['items'] == []
    assert client.get(URL, params={'page': 2, 'page_size': 1}).json()['items'] == []
    assert bank_snapshot(client) == before


def test_owned_completed_saved_payload_analysis_is_monitorable(client):
    analyst_id = login(client)
    manager = app.state.analysis_manager
    analysis_id = str(uuid4())
    records = [{'transaction_id': str(index), 'record_id': str(index), 'risk_probability': 0,
        'risk_level': 'low', 'transaction_timestamp': (NOW + timedelta(minutes=index)).isoformat(),
        'sender_account_id': f'S{index}', 'recipient_account_id': 'TARGET',
        'transaction_amount': 2000, 'currency': 'KZT'} for index in range(12)]
    manager.store.create_universal_session(session_id=analysis_id, client_records=[], transaction_records=records,
        relationships=[], result={}, inventory={}, mapping={}, plan={})
    manager.store.set_owner(analysis_id, analyst_id)
    manager.jobs[analysis_id] = SimpleNamespace(owner_user_id=analyst_id, status='completed', warnings=['Synthetic time'])
    before = bank_snapshot(client)
    response = client.get(URL, params={'analysis_id': analysis_id})
    assert response.status_code == 200, response.text
    assert response.json()['total'] == 1
    assert response.json()['source']['records_scanned'] == 12
    assert 'Synthetic time' in response.json()['source']['warnings']
    assert response.json()['items'][0]['recipient_id'] == 'TARGET'
    assert bank_snapshot(client) == before


def test_real_transaction_runner_preserves_currency_amount_for_saved_monitoring(client, tmp_path):
    from app.risk_ledger.services.analysis_orchestrator import TransactionAnomalyProfileRunner
    analyst_id = login(client)
    path = Path(__file__).resolve().parents[2] / 'examples' / 'recipient_monitoring_demo.csv'
    # The production materializer sorts the canonical source chronologically.
    # Exercise the runner's real input contract rather than the raw upload order.
    with path.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames
        source = sorted(reader, key=lambda record: (record['transaction_timestamp'], record['transaction_id']))
    canonical_path = tmp_path / 'canonical-transactions.csv'
    with canonical_path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(source)
    runner = TransactionAnomalyProfileRunner(canonical_path, tmp_path / 'no-ml-artifacts', chunk_size=7)
    execution = runner.run(cancel_check=lambda: None, report_progress=lambda *args: None)
    assert len(execution.records) == 30
    assert all(record['currency'] == 'KZT' and float(record['transaction_amount']) > 0
               for record in execution.records)
    manager = app.state.analysis_manager
    analysis_id = str(uuid4())
    manager.store.create_universal_session(session_id=analysis_id, client_records=[],
        transaction_records=execution.records, relationships=[], result={}, inventory={}, mapping={}, plan={})
    manager.store.set_owner(analysis_id, analyst_id)
    manager.jobs[analysis_id] = SimpleNamespace(owner_user_id=analyst_id, status='completed', warnings=[])
    response = client.get(URL, params={'analysis_id': analysis_id})
    assert response.status_code == 200, response.text
    assert response.json()['source']['records_scanned'] == 30
    assert response.json()['total'] == 1
    assert response.json()['items'][0]['recipient_id'] == 'RISK_RECEIVER'
    assert response.json()['items'][0]['small_transfer_count'] == 12


def test_legacy_saved_analysis_without_amount_and_currency_requests_rerun(client):
    analyst_id = login(client)
    manager = app.state.analysis_manager
    analysis_id = str(uuid4())
    records = [{'transaction_id': str(index), 'record_id': str(index), 'risk_probability': 0,
        'risk_level': 'low', 'transaction_timestamp': NOW.isoformat(),
        'sender_account_id': f'S{index}', 'recipient_account_id': 'TARGET'} for index in range(12)]
    manager.store.create_universal_session(session_id=analysis_id, client_records=[], transaction_records=records,
        relationships=[], result={}, inventory={}, mapping={}, plan={})
    manager.store.set_owner(analysis_id, analyst_id)
    manager.jobs[analysis_id] = SimpleNamespace(owner_user_id=analyst_id, status='completed', warnings=[])
    response = client.get(URL, params={'analysis_id': analysis_id})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'transaction_metadata_unavailable'
    assert 'Повторите анализ' in response.json()['error']['message']


def test_runner_metadata_join_preserves_duplicate_signatures_for_conflict_exclusion(tmp_path, monkeypatch):
    import pandas as pd
    from app.risk_ledger.services import analysis_orchestrator
    path = Path(__file__).resolve().parents[2] / 'examples' / 'recipient_monitoring_demo.csv'
    with path.open(encoding='utf-8', newline='') as stream:
        source = list(csv.DictReader(stream))[:10]
    scores = [{key: value for key, value in record.items() if key not in {'currency', 'direction', 'transaction_amount'}}
              for record in source]
    for score in scores:
        score.update(risk_signal_score=0, requires_review=False)
    scores.append({**scores[-1], 'sender_account_id': 'CONFLICTING_OTHER_SENDER'})
    monkeypatch.setattr(analysis_orchestrator, 'analyze_transaction_risk',
                        lambda *args, **kwargs: (pd.DataFrame(scores), {'engine_mode': 'rule_based_fallback'}))
    runner = analysis_orchestrator.TransactionAnomalyProfileRunner(path, tmp_path / 'no-model')
    execution = runner.run(cancel_check=lambda: None, report_progress=lambda *args: None)
    duplicates = [record for record in execution.records if record['transaction_id'] == 'R10']
    assert len(duplicates) == 2 and all(record['currency'] == 'KZT' for record in duplicates)
    normalized = [{'id': record['transaction_id'], 'timestamp': record['transaction_timestamp'],
                   'sender_id': record['sender_account_id'], 'recipient_id': record['recipient_account_id'],
                   'amount': record['transaction_amount'], 'currency': record['currency']}
                  for record in execution.records]
    alerts, warnings = detect_recipient_alerts(normalized)
    assert alerts == []
    assert any('конфликтующих ID исключено полностью: 1' in warning for warning in warnings)
