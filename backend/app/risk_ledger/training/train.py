from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import train_test_split

from app.risk_ledger.core.config import (
    DEFAULT_ITERATIONS,
    DEFAULT_RANDOM_SEED,
    DEFAULT_TEST_SIZE,
    MODEL_VERSION,
    TARGET_COLUMN,
)
from app.risk_ledger.core.model_loader import sha256_file
from app.risk_ledger.core.model_loader import load_artifacts
from app.risk_ledger.services.csv_reader import read_csv
from app.risk_ledger.services.client_feature_schema import (
    CLIENT_FEATURE_SCHEMA_VERSION,
    SHAP_DICTIONARY_VERSION,
    canonicalize_client_frame,
)
from app.risk_ledger.services.preprocessing import (
    build_manifest,
    ensure_binary_target,
    find_conflicting_duplicate_features,
    is_identifier_column,
    remove_exact_duplicates,
    transform_features,
)


def _choose_f2_threshold(y_true: pd.Series, probabilities: np.ndarray) -> float:
    precision, recall, thresholds = precision_recall_curve(y_true, probabilities)
    if thresholds.size == 0:
        return 0.5
    precision = precision[:-1]
    recall = recall[:-1]
    denominator = 4 * precision + recall
    scores = np.divide(
        5 * precision * recall,
        denominator,
        out=np.zeros_like(denominator),
        where=denominator != 0,
    )
    return float(thresholds[int(np.nanargmax(scores))])


def _ks_statistic(y_true: pd.Series, probabilities: np.ndarray) -> float:
    false_positive_rate, true_positive_rate, _ = roc_curve(y_true, probabilities)
    return float(np.max(true_positive_rate - false_positive_rate))


def _metric_payload(
    y_true: pd.Series,
    probabilities: np.ndarray,
    threshold: float,
) -> dict[str, Any]:
    predictions = (probabilities >= threshold).astype(int)
    roc_auc = float(roc_auc_score(y_true, probabilities))
    matrix = confusion_matrix(y_true, predictions, labels=[0, 1]).tolist()
    return {
        "gini": 2 * roc_auc - 1,
        "ks": _ks_statistic(y_true, probabilities),
        "accuracy": float(accuracy_score(y_true, predictions)),
        "precision": float(precision_score(y_true, predictions, zero_division=0)),
        "recall": float(recall_score(y_true, predictions, zero_division=0)),
        "roc_auc": roc_auc,
        "pr_auc": float(average_precision_score(y_true, probabilities)),
        "confusion_matrix": matrix,
        "threshold": threshold,
    }


def train_model(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    target_column: str = TARGET_COLUMN,
    random_seed: int = DEFAULT_RANDOM_SEED,
    test_size: float = DEFAULT_TEST_SIZE,
    iterations: int = DEFAULT_ITERATIONS,
    model_version: str = MODEL_VERSION,
    baseline_artifact_dir: str | Path | None = None,
) -> dict[str, Any]:
    frame, csv_metadata = read_csv(input_path)
    if target_column not in frame.columns:
        raise ValueError(f"Training CSV must contain target column {target_column!r}.")

    frame, removed_duplicates = remove_exact_duplicates(frame)
    target = ensure_binary_target(frame[target_column])
    all_raw_features = frame.drop(columns=[target_column])
    technical_identifiers = [
        str(column) for column in all_raw_features.columns if is_identifier_column(str(column))
    ]
    raw_features = all_raw_features.drop(columns=technical_identifiers)

    conflict_count = find_conflicting_duplicate_features(
        frame,
        raw_features.columns,
        target_column,
    )
    if conflict_count:
        raise ValueError(
            f"Found {conflict_count} duplicate feature groups with conflicting targets."
        )

    train_indices, test_indices = train_test_split(
        np.arange(len(frame)),
        test_size=test_size,
        random_state=random_seed,
        stratify=target,
    )
    raw_train = raw_features.iloc[train_indices].reset_index(drop=True)
    raw_test = raw_features.iloc[test_indices].reset_index(drop=True)
    y_train = target.iloc[train_indices].reset_index(drop=True)
    y_test = target.iloc[test_indices].reset_index(drop=True)

    canonical_train, feature_sources, feature_labels = canonicalize_client_frame(raw_train)
    manifest = build_manifest(
        pd.concat([canonical_train, y_train.rename(target_column)], axis=1),
        target_column=target_column,
        model_version=model_version,
        random_seed=random_seed,
    )
    manifest.training_rows = len(raw_train)
    manifest.target_rate = float(y_train.mean())
    manifest.identifier_columns = technical_identifiers
    manifest.canonical_schema_version = CLIENT_FEATURE_SCHEMA_VERSION
    manifest.feature_sources = {
        name: feature_sources[name] for name in manifest.feature_columns
    }
    manifest.feature_labels = {
        name: feature_labels[name] for name in manifest.feature_columns
    }
    manifest.shap_dictionary_version = SHAP_DICTIONARY_VERSION
    manifest.training_source_sha256 = sha256_file(Path(input_path))

    x_train = transform_features(raw_train, manifest)
    x_test = transform_features(raw_test, manifest)
    if target_column in x_train.columns or target_column in manifest.feature_columns:
        raise AssertionError("Target leakage detected in training features.")

    from catboost import CatBoostClassifier
    model = CatBoostClassifier(
        iterations=iterations,
        depth=6,
        learning_rate=0.08,
        loss_function="Logloss",
        eval_metric="AUC",
        auto_class_weights="Balanced",
        random_seed=random_seed,
        allow_writing_files=False,
        verbose=False,
        thread_count=1,
    )
    model.fit(
        x_train,
        y_train,
        cat_features=manifest.categorical_features,
        eval_set=(x_test, y_test),
        use_best_model=False,
    )

    probabilities = model.predict_proba(x_test)[:, 1]
    threshold = _choose_f2_threshold(y_test, probabilities)
    manifest.review_threshold = threshold
    critical_boundary = max(
        threshold,
        min(0.99, max(threshold * 1.5, threshold + 0.10)),
    )
    manifest.risk_boundaries = {
        "medium": max(0.01, min(threshold * 0.5, threshold)),
        "high": threshold,
        "critical": critical_boundary,
    }

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    model_path = output / manifest.model_filename
    model.save_model(str(model_path))
    manifest.model_sha256 = sha256_file(model_path)
    manifest.save(output / "manifest.json")

    shap_dictionary = {
        "schema_version": SHAP_DICTIONARY_VERSION,
        "model_version": model_version,
        "canonical_schema_version": CLIENT_FEATURE_SCHEMA_VERSION,
        "features": {
            name: {
                **manifest.feature_labels[name],
                "source_aliases": manifest.feature_sources[name],
            }
            for name in manifest.feature_columns
        },
    }
    (output / "shap_dictionary.json").write_text(
        json.dumps(shap_dictionary, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    metrics = {
        "model_version": model_version,
        "delimiter": csv_metadata.delimiter,
        "rows_total": len(frame),
        "rows_train": len(raw_train),
        "rows_test": len(raw_test),
        "duplicates_removed": removed_duplicates,
        "target_rate_train": float(y_train.mean()),
        "target_rate_test": float(y_test.mean()),
        "features": len(manifest.feature_columns),
        "categorical_features": len(manifest.categorical_features),
        "canonical_schema_version": CLIENT_FEATURE_SCHEMA_VERSION,
        "training_source_sha256": manifest.training_source_sha256,
        "test": _metric_payload(y_test, probabilities, threshold),
    }

    renamed = raw_test.rename(
        columns={
            source: next(
                (
                    alias
                    for alias in manifest.feature_sources.get(feature, [])
                    if alias not in {source, feature}
                ),
                source,
            )
            for feature, source_aliases in manifest.feature_sources.items()
            for source in source_aliases[:1]
            if source in raw_test.columns
        }
    )
    renamed_probabilities = model.predict_proba(transform_features(renamed, manifest))[:, 1]
    semantic_delta = float(np.max(np.abs(probabilities - renamed_probabilities)))
    regression: dict[str, Any] = {
        "semantic_alias_equivalence": {
            "rows": len(raw_test),
            "max_absolute_probability_delta": semantic_delta,
            "tolerance": 1e-12,
            "passed": semantic_delta <= 1e-12,
        }
    }
    if baseline_artifact_dir is not None:
        baseline_model, baseline_manifest = load_artifacts(baseline_artifact_dir)
        baseline_probabilities = baseline_model.predict_proba(
            transform_features(raw_test, baseline_manifest)
        )[:, 1]
        baseline_threshold = baseline_manifest.review_threshold
        baseline_metrics = _metric_payload(y_test, baseline_probabilities, baseline_threshold)
        candidate_metrics = metrics["test"]
        probability_delta = float(np.max(np.abs(probabilities - baseline_probabilities)))
        regression["baseline_comparison"] = {
            "baseline_model_version": baseline_manifest.model_version,
            "candidate_model_version": model_version,
            "rows": len(raw_test),
            "max_absolute_probability_delta": probability_delta,
            "metric_deltas": {
                key: float(candidate_metrics[key] - baseline_metrics[key])
                for key in ("gini", "ks", "accuracy", "precision", "recall", "roc_auc", "pr_auc")
            },
            "baseline_test": baseline_metrics,
            "passed": (
                candidate_metrics["roc_auc"] >= baseline_metrics["roc_auc"] - 0.02
                and candidate_metrics["pr_auc"] >= baseline_metrics["pr_auc"] - 0.02
                and candidate_metrics["recall"] >= baseline_metrics["recall"] - 0.05
            ),
        }
    metrics["regression"] = regression
    (output / "regression.json").write_text(
        json.dumps(regression, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    (output / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return metrics


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train the local CatBoost fraud model.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target", default=TARGET_COLUMN)
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--test-size", type=float, default=DEFAULT_TEST_SIZE)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--model-version", default=MODEL_VERSION)
    parser.add_argument("--baseline-artifacts", type=Path)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    metrics = train_model(
        args.input,
        args.output,
        target_column=args.target,
        random_seed=args.seed,
        test_size=args.test_size,
        iterations=args.iterations,
        model_version=args.model_version,
        baseline_artifact_dir=args.baseline_artifacts,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
