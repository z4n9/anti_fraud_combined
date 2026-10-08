from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path

import pytest

from app.risk_ledger.domain.models import AnalysisMetrics
from app.risk_ledger.services.session_store import SessionNotFoundError, SessionStore


def _rows() -> list[dict]:
    return [
        {
            "source": name,
            "record_id": f"row-{index}",
            "risk_probability": probability,
            "risk_level": level,
            "requires_review": probability >= 0.5,
            "explanation_factors": [{"feature": "x", "contribution": probability}],
            "analysis_warnings": [],
        }
        for index, (name, probability, level) in enumerate(
            [
                ("a", 0.2, "low"),
                ("b", 0.95, "critical"),
                ("c", 0.7, "high"),
                ("d", 0.6, "high"),
            ],
            start=1,
        )
    ]


def test_sort_filter_paginate_threshold_and_stream_csv(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    metrics = AnalysisMetrics(available=False, threshold=0.5)
    session_id = store.create_session(
        _rows(), threshold=0.5, metrics=metrics, model_version="test"
    )

    first_page = store.get_page(session_id, page=1, page_size=2)
    second_page = store.get_page(session_id, page=2, page_size=2)
    high = store.get_page(session_id, risk_filter="high", page_size=10)
    stricter = store.get_page(session_id, threshold=0.8, page_size=10)

    assert [item["source"] for item in first_page.items] == ["b", "c"]
    assert [item["source"] for item in second_page.items] == ["d", "a"]
    assert [item["source"] for item in high.items] == ["c", "d"]
    assert [item["requires_review"] for item in stricter.items] == [
        True,
        False,
        False,
        False,
    ]

    report = "".join(store.iter_csv(session_id, threshold=0.8))
    parsed = list(csv.DictReader(io.StringIO(report)))
    assert [row["source"] for row in parsed] == ["b", "c", "d", "a"]
    assert parsed[0]["requires_review"] == "True"
    assert parsed[1]["requires_review"] == "False"
    assert isinstance(json.loads(parsed[0]["explanation_factors"]), list)


def test_extended_filters_and_distribution(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session_id = store.create_session(_rows(), threshold=0.5)

    reviewed = store.get_page(session_id, requires_review=True, page_size=10)
    probability = store.get_page(
        session_id, probability_min=0.6, probability_max=0.7, page_size=10
    )
    record = store.get_page(session_id, record_id="ROW-2", page_size=10)
    escaped_wildcard = store.get_page(session_id, record_id="row_%", page_size=10)

    assert [item["source"] for item in reviewed.items] == ["b", "c", "d"]
    assert [item["source"] for item in probability.items] == ["c", "d"]
    assert [item["source"] for item in record.items] == ["b"]
    assert escaped_wildcard.total == 0

    distribution = store.get_distribution(session_id, bins=5)
    assert distribution.risk_counts == {
        "low": 1,
        "medium": 0,
        "high": 2,
        "critical": 1,
    }
    assert [item.count for item in distribution.probability_histogram] == [0, 1, 0, 2, 1]
    assert sum(item.count for item in distribution.probability_histogram) == 4

    with pytest.raises(ValueError, match="must not exceed"):
        store.get_page(session_id, probability_min=0.8, probability_max=0.2)


def test_delete_and_startup_cleanup_remove_all_temporary_files(tmp_path: Path) -> None:
    store = SessionStore(tmp_path, ttl_seconds=10)
    first = store.create_session(_rows(), threshold=0.5)
    upload = store.save_upload(first, b"a,b\n1,2\n")
    assert upload.is_file()

    store.delete_session(first)
    assert not upload.parent.exists()
    with pytest.raises(SessionNotFoundError):
        store.get_page(first)

    expired = store.create_session(_rows(), threshold=0.5)
    metadata_path = tmp_path / expired / "results.sqlite3"
    import sqlite3

    connection = sqlite3.connect(metadata_path)
    try:
        connection.execute(
            "UPDATE session_metadata SET value = ? WHERE key = 'created_at'",
            ("0",),
        )
        connection.commit()
    finally:
        connection.close()
    assert store.cleanup_expired(now=100) == [expired]
    assert not (tmp_path / expired).exists()


def test_stream_csv_can_resume_on_another_worker_thread(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session_id = store.create_session(_rows(), threshold=0.5)
    stream = store.iter_csv(session_id)

    with (
        ThreadPoolExecutor(max_workers=1) as first_worker,
        ThreadPoolExecutor(max_workers=1) as second_worker,
    ):
        header = first_worker.submit(next, stream).result()
        first_row = second_worker.submit(next, stream).result()

    report = header + first_row + "".join(stream)
    parsed = list(csv.DictReader(io.StringIO(report)))
    assert [row["source"] for row in parsed] == ["b", "c", "d", "a"]
