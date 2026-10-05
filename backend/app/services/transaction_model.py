from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.services.transaction_features import (
    TransactionFeatureReference,
    iter_feature_batches,
)


class TransactionArtifactError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_transaction_artifacts(directory: str | Path) -> tuple[Any, dict[str, Any]]:
    """Load trusted, locally configured model artifacts; ML dependencies are optional."""
    try:
        return _load_transaction_artifacts(directory)
    except TransactionArtifactError:
        raise
    except Exception as exc:
        raise TransactionArtifactError("Transaction artifacts are invalid or their dependencies are unavailable.") from exc


def _load_transaction_artifacts(directory: str | Path) -> tuple[Any, dict[str, Any]]:
    import joblib

    artifact_dir = Path(directory)
    manifest_path = artifact_dir / "manifest.json"
    if not manifest_path.is_file():
        raise TransactionArtifactError(f"Missing transaction manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model_path = artifact_dir / str(manifest.get("model_filename", "model.joblib"))
    if not model_path.is_file():
        raise TransactionArtifactError(f"Missing transaction model: {model_path}")
    expected = str(manifest.get("model_sha256", ""))
    if expected and sha256_file(model_path) != expected:
        raise TransactionArtifactError("Transaction model checksum does not match manifest.")
    model = joblib.load(model_path)
    if list(getattr(model, "feature_names_in_", ())) != list(manifest["feature_columns"]):
        raise TransactionArtifactError("Transaction model feature order does not match manifest.")
    reference = TransactionFeatureReference.from_dict(manifest["feature_reference"])
    if manifest["feature_columns"] != reference.feature_columns:
        raise TransactionArtifactError("Transaction reference feature order does not match manifest.")
    if not np.isfinite(reference.amount_median) or not np.isfinite(reference.amount_mad) or reference.amount_mad <= 0:
        raise TransactionArtifactError("Invalid transaction reference amounts.")
    normalize_anomaly_scores(np.array([0.0]), manifest["score_normalization"])
    if not 0 <= float(manifest["review_threshold"]) <= 1:
        raise TransactionArtifactError("Invalid transaction review threshold.")
    if not manifest["model_version"]:
        raise TransactionArtifactError("Missing transaction model version.")
    for feature in manifest["feature_columns"]:
        profile = manifest["feature_profiles"][feature]
        if (not np.isfinite(float(profile["median"]))
                or not np.isfinite(float(profile["scale"])) or float(profile["scale"]) <= 0):
            raise TransactionArtifactError("Invalid transaction feature profile.")
        if not manifest["feature_labels"][feature]:
            raise TransactionArtifactError("Missing transaction feature label.")
    return model, manifest


def normalize_anomaly_scores(raw_scores: np.ndarray, normalization: dict[str, float]) -> np.ndarray:
    lower = float(normalization["lower"])
    upper = float(normalization["upper"])
    if not np.isfinite(lower) or not np.isfinite(upper) or upper <= lower:
        raise ValueError("Invalid anomaly-score normalization boundaries.")
    return np.clip((np.asarray(raw_scores, dtype="float64") - lower) / (upper - lower), 0, 1)


def explain_feature_deviations(
    features: pd.DataFrame,
    manifest: dict[str, Any],
    *,
    count: int = 5,
) -> list[list[dict[str, Any]]]:
    profiles = manifest["feature_profiles"]
    labels = manifest["feature_labels"]
    explanations: list[list[dict[str, Any]]] = []
    for _, row in features.iterrows():
        ranked: list[tuple[float, str, float]] = []
        for feature in manifest["feature_columns"]:
            value = float(row[feature])
            profile = profiles[feature]
            scale = max(float(profile["scale"]), 1e-12)
            deviation = (value - float(profile["median"])) / scale
            ranked.append((abs(deviation), feature, deviation))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        explanations.append(
            [
                {
                    "feature": feature,
                    "label": labels[feature],
                    "value": float(row[feature]),
                    "deviation_from_normal": float(deviation),
                    "direction": "above_normal" if deviation >= 0 else "below_normal",
                    "explanation_type": "deviation_from_training_norm",
                }
                for _, feature, deviation in ranked[:count]
            ]
        )
    return explanations


def score_transaction_file(
    input_path: str | Path,
    artifact_dir: str | Path,
    *,
    chunk_size: int = 2_000,
    cancel_check: Callable[[], None] | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> pd.DataFrame:
    model, manifest = load_transaction_artifacts(artifact_dir)
    reference = TransactionFeatureReference.from_dict(manifest["feature_reference"])
    batches: list[pd.DataFrame] = []
    processed = 0
    for features, source in iter_feature_batches(input_path, reference, chunk_size=chunk_size):
        if cancel_check is not None:
            cancel_check()
        try:
            raw = -np.asarray(model.score_samples(features), dtype="float64")
            if raw.shape != (len(features),) or not np.isfinite(raw).all():
                raise ValueError("Invalid model score output.")
        except Exception as exc:
            raise TransactionArtifactError("Transaction model could not score the feature batch.") from exc
        scores = normalize_anomaly_scores(raw, manifest["score_normalization"])
        explanations = explain_feature_deviations(features, manifest)
        output = pd.DataFrame(
            {
                "transaction_id": source["transaction_id"].astype(str).to_numpy(),
                "anomaly_score": scores,
                "requires_review": scores >= float(manifest["review_threshold"]),
                "ml_explanation": [json.dumps(item, ensure_ascii=False) for item in explanations],
            },
            index=source.index,
        )
        for control in ("scenario", "is_anomaly", "synthetic_only"):
            if control in source.columns:
                output[control] = source[control].to_numpy()
        batches.append(output)
        processed += len(source)
        if progress_callback is not None:
            progress_callback(processed)
    if cancel_check is not None:
        cancel_check()
    result = pd.concat(batches, ignore_index=True)
    result = result.sort_values(
        ["anomaly_score", "transaction_id"], ascending=[False, True], kind="stable"
    ).reset_index(drop=True)
    result.insert(0, "rank", np.arange(1, len(result) + 1))
    return result
