from __future__ import annotations

from pathlib import Path

from app.risk_ledger.services.session_store import SessionStore


def test_only_human_decisions_create_anonymized_reproducible_labels(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session_id = store.create_session(
        [{
            "record_id": "client-sensitive-123",
            "risk_probability": 0.91,
            "risk_level": "critical",
            "requires_review": True,
            "explanation_factors": [],
            "analysis_warnings": [],
        }],
        threshold=0.5,
        model_version="test-model",
    )

    started = store.save_feedback(session_id, "client-sensitive-123", "in_review", "Проверка начата")
    assert started["confirmed_label"] is None
    assert store.feedback_summary(session_id)["confirmed_labels"] == []

    confirmed = store.save_feedback(session_id, "client-sensitive-123", "confirmed", "Документы проверены")
    assert confirmed["confirmed_label"]["human_label"] == 1
    assert confirmed["confirmed_label"]["source"] == "human_confirmed"
    anonymized = store.feedback_summary(session_id)["confirmed_labels"][0]
    assert anonymized["entity_key"] != "client-sensitive-123"
    assert len(anonymized["entity_key"]) == 64
    accumulated = store.confirmed_feedback_labels()
    assert accumulated[0]["source"] == "human_confirmed"
    assert accumulated[0]["entity_key"] == anonymized["entity_key"]

    dismissed = store.save_feedback(session_id, "client-sensitive-123", "dismissed", "Ложная тревога")
    assert dismissed["confirmed_label"]["human_label"] == 0
    assert [event["status"] for event in dismissed["history"]] == [
        "dismissed", "confirmed", "in_review",
    ]


def test_returning_to_unconfirmed_status_removes_active_training_label(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session_id = store.create_session(
        [{"record_id": "row-1", "risk_probability": 0.7, "risk_level": "high"}],
        threshold=0.5,
    )
    store.save_feedback(session_id, "row-1", "confirmed", "Подтверждено")
    restored = store.save_feedback(session_id, "row-1", "in_review", "Нужна повторная проверка")
    assert restored["confirmed_label"] is None
    assert store.feedback_summary(session_id)["total_confirmed"] == 0
    assert len(restored["history"]) == 2
    assert store.confirmed_feedback_labels() == []


def test_confirmed_label_survives_temporary_session_deletion(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session_id = store.create_session(
        [{"record_id": "row-keep", "risk_probability": 0.8, "risk_level": "high"}],
        threshold=0.5,
    )
    store.save_feedback(session_id, "row-keep", "confirmed", "Проверено")
    store.delete_session(session_id)
    labels = store.confirmed_feedback_labels()
    assert len(labels) == 1
    assert labels[0]["human_label"] == 1
    assert labels[0]["entity_key"] != "row-keep"
