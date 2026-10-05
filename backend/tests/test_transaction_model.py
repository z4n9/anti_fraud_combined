from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.services.transaction_features import (
    CONTROL_ONLY_COLUMNS,
    TransactionFeatureBuilder,
    TransactionFeatureReference,
    fit_transaction_reference,
    iter_feature_batches,
)
from app.services.transaction_model import (
    TransactionArtifactError,
    load_transaction_artifacts,
    score_transaction_file,
)
from app.training.generate_synthetic_transactions import generate_synthetic_dataset
from app.training.train_transactions import train_transaction_model


def _reference() -> TransactionFeatureReference:
    return TransactionFeatureReference(
        schema_version="1.0",
        amount_median=100.0,
        amount_mad=20.0,
        categories={
            "currency": ["KZT"],
            "channel": ["mobile"],
            "country": ["KZ"],
            "direction": ["outbound", "inbound"],
        },
    )


def _row(timestamp: str, amount: float, recipient: str = "SYN-RC-000001") -> dict:
    return {
        "transaction_id": f"SYN-TX-{timestamp}",
        "client_id": "SYN-CL-000001",
        "sender_account_id": "SYN-AC-000001",
        "recipient_account_id": recipient,
        "transaction_timestamp": timestamp,
        "transaction_amount": amount,
        "currency": "KZT",
        "channel": "mobile",
        "country": "KZ",
        "direction": "outbound",
        "balance_before": 1_000.0,
    }


def test_window_and_historical_features_use_only_prior_transactions() -> None:
    frame = pd.DataFrame(
        [
            _row("2026-01-01T10:00:00Z", 100),
            _row("2026-01-01T10:03:00Z", 120),
            _row("2026-01-01T11:10:00Z", 300, "SYN-RC-000002"),
        ]
    )
    features = TransactionFeatureBuilder(_reference()).transform_chunk(frame)

    assert features["prior_count_5m"].tolist() == [0.0, 1.0, 0.0]
    assert features["prior_count_1h"].tolist() == [0.0, 1.0, 0.0]
    assert features["prior_count_1d"].tolist() == [0.0, 1.0, 2.0]
    assert features["minutes_since_previous"].tolist() == [43_200.0, 3.0, 67.0]
    assert features["is_new_recipient"].tolist() == [1.0, 0.0, 1.0]
    assert not (CONTROL_ONLY_COLUMNS & set(features.columns))


def test_chunked_feature_build_is_invariant(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    generate_synthetic_dataset(fixture, seed=91, normal_rows=1_000)
    source = fixture / "transactions.csv"
    reference = fit_transaction_reference(source, chunk_size=113)

    small = pd.concat(
        [features for features, _ in iter_feature_batches(source, reference, chunk_size=37)],
        ignore_index=True,
    )
    large = pd.concat(
        [features for features, _ in iter_feature_batches(source, reference, chunk_size=5_000)],
        ignore_index=True,
    )

    assert list(small.columns) == reference.feature_columns
    assert np.allclose(small.to_numpy(), large.to_numpy(), rtol=0, atol=1e-12)
    assert np.isfinite(small.to_numpy()).all()


def test_training_creates_versioned_synthetic_only_artifacts(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    artifacts = tmp_path / "artifacts"
    generate_synthetic_dataset(fixture, seed=101, normal_rows=1_000)

    metrics = train_transaction_model(
        fixture / "transactions.csv",
        artifacts,
        model_version="transaction-test",
        random_seed=101,
        estimators=40,
        chunk_size=127,
    )

    assert {path.name for path in artifacts.iterdir()} == {
        "manifest.json",
        "metrics.json",
        "model.joblib",
        "scores.csv",
    }
    model, manifest = load_transaction_artifacts(artifacts)
    assert model is not None
    assert manifest["model_version"] == "transaction-test"
    assert manifest["algorithm"] == "IsolationForest"
    assert manifest["training_data"]["synthetic_only"] is True
    assert manifest["training_data"]["control_labels_used_as_features"] is False
    assert not (CONTROL_ONLY_COLUMNS & set(manifest["feature_columns"]))
    assert metrics["synthetic_only"] is True
    assert metrics["suitable_for_real_quality_claims"] is False
    assert metrics["mean_score_control_anomaly"] > metrics["mean_score_normal"]

    scores = pd.read_csv(artifacts / "scores.csv")
    assert scores["anomaly_score"].is_monotonic_decreasing
    assert scores["rank"].tolist() == list(range(1, len(scores) + 1))
    explanation = json.loads(scores.iloc[0]["ml_explanation"])
    assert 1 <= len(explanation) <= 5
    assert all(item["explanation_type"] == "deviation_from_training_norm" for item in explanation)


def test_scoring_is_reproducible_across_batch_sizes(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    artifacts = tmp_path / "artifacts"
    generate_synthetic_dataset(fixture, seed=103, normal_rows=1_000)
    source = fixture / "transactions.csv"
    train_transaction_model(source, artifacts, estimators=30, chunk_size=101)

    small = score_transaction_file(source, artifacts, chunk_size=31)
    large = score_transaction_file(source, artifacts, chunk_size=5_000)

    assert small["transaction_id"].tolist() == large["transaction_id"].tolist()
    assert np.allclose(small["anomaly_score"], large["anomaly_score"], rtol=0, atol=1e-12)
    assert small["anomaly_score"].is_monotonic_decreasing


def test_transaction_model_loader_rejects_checksum_mismatch(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    artifacts = tmp_path / "artifacts"
    generate_synthetic_dataset(fixture, seed=105, normal_rows=1_000)
    train_transaction_model(fixture / "transactions.csv", artifacts, estimators=10)
    with (artifacts / "model.joblib").open("ab") as stream:
        stream.write(b"corruption")

    with pytest.raises(TransactionArtifactError, match="checksum"):
        load_transaction_artifacts(artifacts)
