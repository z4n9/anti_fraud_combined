from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score

from app.risk_ledger.services.transaction_features import (
    CONTROL_ONLY_COLUMNS,
    TransactionFeatureReference,
    fit_transaction_reference,
    iter_feature_batches,
)
from app.risk_ledger.services.transaction_model import (
    explain_feature_deviations,
    normalize_anomaly_scores,
    sha256_file,
)


TRANSACTION_MODEL_VERSION = "1.0.0"
DEFAULT_TRANSACTION_SEED = 20260928
DEFAULT_ESTIMATORS = 300


def _feature_profiles(features: pd.DataFrame) -> dict[str, dict[str, float]]:
    profiles: dict[str, dict[str, float]] = {}
    for column in features.columns:
        values = features[column].to_numpy(dtype="float64")
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        if mad <= 1e-12:
            mad = float(np.std(values))
        profiles[column] = {"median": median, "scale": max(mad, 1e-6)}
    return profiles


def _load_feature_matrix(
    input_path: str | Path,
    reference: TransactionFeatureReference,
    *,
    chunk_size: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    feature_batches: list[pd.DataFrame] = []
    control_batches: list[pd.DataFrame] = []
    for features, source in iter_feature_batches(input_path, reference, chunk_size=chunk_size):
        controls = pd.DataFrame(index=source.index)
        controls["transaction_id"] = source["transaction_id"].astype(str)
        for name in CONTROL_ONLY_COLUMNS:
            if name in source.columns:
                controls[name] = source[name]
        feature_batches.append(features)
        control_batches.append(controls)
    return (
        pd.concat(feature_batches, ignore_index=True),
        pd.concat(control_batches, ignore_index=True),
    )


def train_transaction_model(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    model_version: str = TRANSACTION_MODEL_VERSION,
    random_seed: int = DEFAULT_TRANSACTION_SEED,
    estimators: int = DEFAULT_ESTIMATORS,
    chunk_size: int = 2_000,
) -> dict[str, Any]:
    input_path = Path(input_path)
    reference = fit_transaction_reference(input_path, chunk_size=chunk_size)
    features, controls = _load_feature_matrix(
        input_path,
        reference,
        chunk_size=chunk_size,
    )
    if CONTROL_ONLY_COLUMNS & set(features.columns):
        raise AssertionError("Synthetic control columns leaked into transaction features.")
    if "is_anomaly" in controls:
        synthetic_labels = pd.to_numeric(controls["is_anomaly"], errors="coerce").fillna(0).astype(int)
        training_mask = synthetic_labels.eq(0)
    else:
        synthetic_labels = pd.Series(0, index=features.index, dtype="int8")
        training_mask = pd.Series(True, index=features.index)
    training_features = features.loc[training_mask].reset_index(drop=True)
    if len(training_features) < 500:
        raise ValueError("At least 500 normal transactions are required for training.")

    model = IsolationForest(
        n_estimators=estimators,
        max_samples=min(512, len(training_features)),
        contamination="auto",
        random_state=random_seed,
        n_jobs=1,
    )
    model.fit(training_features)
    normal_raw = -np.asarray(model.score_samples(training_features), dtype="float64")
    lower = float(np.quantile(normal_raw, 0.01))
    upper = float(np.quantile(normal_raw, 0.999))
    if upper <= lower:
        raise ValueError("Training scores cannot be normalized safely.")
    normalization = {"method": "training_quantile_minmax", "lower": lower, "upper": upper}
    normal_scores = normalize_anomaly_scores(normal_raw, normalization)
    review_threshold = float(np.quantile(normal_scores, 0.99))

    all_raw = -np.asarray(model.score_samples(features), dtype="float64")
    all_scores = normalize_anomaly_scores(all_raw, normalization)
    predictions = all_scores >= review_threshold
    feature_profiles = _feature_profiles(training_features)

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    model_path = output / "model.joblib"
    joblib.dump(model, model_path, compress=3)
    manifest: dict[str, Any] = {
        "manifest_schema_version": "1.0",
        "model_version": model_version,
        "profile": "transaction_anomaly",
        "algorithm": "IsolationForest",
        "random_seed": random_seed,
        "estimators": estimators,
        "training_rows": int(training_mask.sum()),
        "scored_rows": len(features),
        "feature_schema_version": reference.schema_version,
        "feature_columns": list(features.columns),
        "feature_labels": reference.feature_labels,
        "feature_profiles": feature_profiles,
        "feature_reference": reference.to_dict(),
        "score_direction": "higher_means_more_anomalous",
        "score_normalization": normalization,
        "review_threshold": review_threshold,
        "model_filename": model_path.name,
        "model_sha256": sha256_file(model_path),
        "training_data": {
            "origin": "synthetic",
            "synthetic_only": True,
            "control_labels_used_as_features": False,
            "control_labels_used_for_model_fit": False,
            "suitable_for_real_quality_claims": False,
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    explanations = explain_feature_deviations(features, manifest)
    scores = pd.DataFrame(
        {
            "transaction_id": controls["transaction_id"],
            "anomaly_score": all_scores,
            "requires_review": predictions,
            "ml_explanation": [json.dumps(item, ensure_ascii=False) for item in explanations],
        }
    )
    for control in ("scenario", "is_anomaly", "synthetic_only"):
        if control in controls:
            scores[control] = controls[control]
    scores = scores.sort_values(
        ["anomaly_score", "transaction_id"], ascending=[False, True], kind="stable"
    ).reset_index(drop=True)
    scores.insert(0, "rank", np.arange(1, len(scores) + 1))
    scores.to_csv(output / "scores.csv", index=False, encoding="utf-8", lineterminator="\n")

    synthetic_metrics: dict[str, Any] = {
        "data_origin": "synthetic",
        "synthetic_only": True,
        "suitable_for_real_quality_claims": False,
        "warning": "Контрольные метрики показывают работу fixture, а не качество на реальных операциях.",
        "rows": len(features),
        "normal_rows": int(training_mask.sum()),
        "control_anomaly_rows": int(synthetic_labels.sum()),
        "review_threshold": review_threshold,
        "review_rows": int(predictions.sum()),
    }
    if synthetic_labels.nunique() == 2:
        synthetic_metrics.update(
            {
                "control_roc_auc": float(roc_auc_score(synthetic_labels, all_scores)),
                "control_pr_auc": float(average_precision_score(synthetic_labels, all_scores)),
                "control_precision": float(precision_score(synthetic_labels, predictions, zero_division=0)),
                "control_recall": float(recall_score(synthetic_labels, predictions, zero_division=0)),
                "mean_score_normal": float(all_scores[synthetic_labels.eq(0)].mean()),
                "mean_score_control_anomaly": float(all_scores[synthetic_labels.eq(1)].mean()),
            }
        )
    (output / "metrics.json").write_text(
        json.dumps(synthetic_metrics, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return synthetic_metrics


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train the transaction Isolation Forest.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-version", default=TRANSACTION_MODEL_VERSION)
    parser.add_argument("--seed", type=int, default=DEFAULT_TRANSACTION_SEED)
    parser.add_argument("--estimators", type=int, default=DEFAULT_ESTIMATORS)
    parser.add_argument("--chunk-size", type=int, default=2_000)
    return parser


def main() -> None:
    args = _parser().parse_args()
    metrics = train_transaction_model(
        args.input,
        args.output,
        model_version=args.model_version,
        random_seed=args.seed,
        estimators=args.estimators,
        chunk_size=args.chunk_size,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
