from __future__ import annotations

import pytest
pytest.importorskip('catboost', reason='Optional CatBoost dependency is absent; no client model is fabricated.')

import json
from pathlib import Path

import pandas as pd

from app.risk_ledger.core.model_loader import load_artifacts
from app.risk_ledger.domain.models import ModelManifest
from app.risk_ledger.services.preprocessing import transform_features
from app.risk_ledger.training.train import train_model


def synthetic_training_frame(rows: int = 160) -> pd.DataFrame:
    records = []
    for index in range(rows):
        target = 1 if index % 8 == 0 else 0
        records.append(
            {
                " amount ": 500_000 + index * 1_000 + target * 300_000,
                "term": 180 + index % 24,
                "GENDER": "516" if index % 2 else "515",
                "NUM_CONTRACTS": "-" if index % 5 == 0 else str(index % 7 + 1),
                "risk_signal": target * 10 + index % 3,
                "unused": "-",
                "GB_flag": target,
            }
        )
    return pd.DataFrame.from_records(records)


def test_training_creates_loadable_versioned_artifacts(tmp_path: Path) -> None:
    input_path = tmp_path / "train.csv"
    output_path = tmp_path / "artifacts"
    synthetic_training_frame().to_csv(input_path, sep=";", index=False)

    metrics = train_model(
        input_path,
        output_path,
        iterations=12,
        random_seed=17,
        model_version="test-1",
    )

    assert {path.name for path in output_path.iterdir()} == {
        "model.cbm",
        "manifest.json",
        "metrics.json",
        "regression.json",
        "shap_dictionary.json",
    }
    manifest = ModelManifest.load(output_path / "manifest.json")
    assert manifest.model_version == "test-1"
    assert manifest.random_seed == 17
    assert "GB_flag" not in manifest.feature_columns
    assert manifest.canonical_schema_version == "1.0"
    assert "client.attributes.amount" in manifest.feature_columns
    assert manifest.feature_sources["client.attributes.amount"][0] == "amount"
    assert "client.attributes.unused" in manifest.dropped_features
    assert 0 < manifest.review_threshold < 1
    assert metrics["rows_train"] == 112
    assert metrics["rows_test"] == 48
    assert {
        "gini",
        "ks",
        "accuracy",
        "precision",
        "recall",
        "roc_auc",
        "pr_auc",
        "confusion_matrix",
        "threshold",
    }.issubset(metrics["test"])

    model, loaded_manifest = load_artifacts(output_path)
    sample = synthetic_training_frame(4).rename(columns={" amount ": "amount"})
    features = transform_features(sample, loaded_manifest)
    probabilities = model.predict_proba(features)[:, 1]
    assert len(probabilities) == 4
    assert ((probabilities >= 0) & (probabilities <= 1)).all()

    serialized = json.loads((output_path / "metrics.json").read_text(encoding="utf-8"))
    assert serialized["model_version"] == "test-1"
    assert serialized["regression"]["semantic_alias_equivalence"]["passed"]


def test_semantically_renamed_fields_produce_equivalent_predictions(tmp_path: Path) -> None:
    input_path = tmp_path / "train.csv"
    output_path = tmp_path / "artifacts"
    synthetic_training_frame().to_csv(input_path, sep=";", index=False)
    train_model(input_path, output_path, iterations=8, random_seed=23)
    model, manifest = load_artifacts(output_path)

    original = synthetic_training_frame(12).drop(columns=["GB_flag"])
    renamed = original.rename(
        columns={
            "term": "loan_term",
            "GENDER": "sex",
            "NUM_CONTRACTS": "contract_count",
        }
    )
    original_probabilities = model.predict_proba(
        transform_features(original, manifest)
    )[:, 1]
    renamed_probabilities = model.predict_proba(
        transform_features(renamed, manifest)
    )[:, 1]

    assert (abs(original_probabilities - renamed_probabilities) <= 1e-12).all()


def test_training_split_is_reproducible(tmp_path: Path) -> None:
    input_path = tmp_path / "train.csv"
    synthetic_training_frame().to_csv(input_path, sep=",", index=False)

    first = train_model(input_path, tmp_path / "first", iterations=8, random_seed=9)
    second = train_model(input_path, tmp_path / "second", iterations=8, random_seed=9)

    assert first["target_rate_train"] == second["target_rate_train"]
    assert first["target_rate_test"] == second["target_rate_test"]
    assert first["test"] == second["test"]
