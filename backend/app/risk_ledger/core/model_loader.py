from __future__ import annotations

import hashlib
from pathlib import Path


from app.risk_ledger.domain.models import ModelManifest


class ModelArtifactError(RuntimeError):
    """Raised when a model artifact is incomplete or inconsistent."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_artifacts(directory: str | Path) -> tuple[CatBoostClassifier, ModelManifest]:
    artifact_dir = Path(directory)
    manifest_path = artifact_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ModelArtifactError(f"Missing manifest: {manifest_path}")

    manifest = ModelManifest.load(manifest_path)
    model_path = (artifact_dir / manifest.model_filename).resolve()
    if artifact_dir.resolve() not in model_path.parents:
        raise ModelArtifactError("Model filename leaves its artifact directory.")
    if not model_path.is_file():
        raise ModelArtifactError(f"Missing model: {model_path}")
    if manifest.model_sha256 and sha256_file(model_path) != manifest.model_sha256:
        raise ModelArtifactError("Model checksum does not match manifest.")

    try:
        from catboost import CatBoostClassifier
    except ImportError as error:
        raise ModelArtifactError("Optional CatBoost dependency is unavailable.") from error
    model = CatBoostClassifier()
    model.load_model(str(model_path))
    model_features = list(model.feature_names_)
    if model_features and model_features != manifest.feature_columns:
        raise ModelArtifactError("Model feature order does not match manifest.")
    return model, manifest
