"""Risk Ledger lifecycle composed into the bank application, with optional ML."""
import asyncio
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from app.risk_ledger.api.routes import AnalysisManager
from app.risk_ledger.core.config import (
    DEFAULT_ARTIFACT_DIR, DEFAULT_DICTIONARY_PATH, DEFAULT_MODEL_REGISTRY_DIR,
    DEFAULT_SESSION_DIR, DEFAULT_TRANSACTION_ARTIFACT_DIR, MAX_INPUT_BYTES, MAX_INPUT_RECORDS,
)
from app.risk_ledger.core.model_loader import load_artifacts
from app.risk_ledger.domain.contracts import AnalysisPlanContract, AnalysisResultContract, SemanticMappingContract, SourceFormat, SourceInventoryContract
from app.risk_ledger.services.model_registry import ModelRegistry
from app.risk_ledger.services.semantic_dictionary import SemanticDictionary
from app.risk_ledger.services.session_store import SessionNotFoundError, SessionStore
from app.risk_ledger.services.source_upload import cleanup_staging_root
from app.services.transaction_model import load_transaction_artifacts


def _restore_owned_results(manager):
    """Completed results remain owned and accessible after a process restart."""
    for directory in manager.store.root.iterdir():
        if not directory.is_dir() or not (directory / "results.sqlite3").is_file():
            continue
        try:
            meta = manager.store.get_metadata(directory.name)
            owner = meta.get("owner_user_id")
            if not isinstance(owner, int):
                continue
            inventory = SourceInventoryContract.model_validate(meta["inventory"]) if meta.get("inventory") else None
            mapping = SemanticMappingContract.model_validate(meta["mapping"]) if meta.get("mapping") else None
            plan = AnalysisPlanContract.model_validate(meta["plan"]) if meta.get("plan") else None
            result = AnalysisResultContract.model_validate(meta["universal_result"]) if meta.get("universal_result") else None
            job = manager.add_job(directory.name, inventory.filename if inventory else "Сохранённый анализ",
                inventory.source_format if inventory else SourceFormat.CSV,
                manager.incoming_dir / directory.name / "source.csv", 0, owner_user_id=owner)
            job.status, job.stage, job.progress = "completed", "ready", 100
            job.inventory = inventory
            job.mapping, job.plan, job.result = mapping, plan, result
        except (ValueError, KeyError, OSError, sqlite3.Error, SessionNotFoundError):
            # Malformed/unowned legacy sessions never become visible to a user.
            continue


@asynccontextmanager
async def analyst_lifespan(app, *, model=None, manifest=None, artifact_dir=DEFAULT_ARTIFACT_DIR,
                           session_store=None, session_dir=DEFAULT_SESSION_DIR,
                           max_input_bytes=MAX_INPUT_BYTES, max_input_records=MAX_INPUT_RECORDS,
                           dictionary_path=DEFAULT_DICTIONARY_PATH,
                           transaction_artifact_dir=DEFAULT_TRANSACTION_ARTIFACT_DIR,
                           model_registry_dir=DEFAULT_MODEL_REGISTRY_DIR):
    # Several TestClient/browser-session contexts can share one application.
    if getattr(app.state, "analyst_lifespan_active", False):
        yield
        return
    store = session_store or SessionStore(session_dir)
    store.cleanup_expired()
    incoming = store.root / "_incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    cleanup_staging_root(incoming)
    artifact_dir = Path(artifact_dir).resolve()
    registry = ModelRegistry(model_registry_dir, candidate_root=artifact_dir.parent)
    if (model is None) != (manifest is None):
        raise ValueError("Model and manifest must be provided together.")
    loaded_model, loaded_manifest = model, manifest
    if loaded_model is None:
        try:
            champion = registry.champion("client_risk")
            client_path = Path(registry.get_version("client_risk", champion["version"])["artifact_path"]) if champion else artifact_dir
            loaded_model, loaded_manifest = load_artifacts(client_path)
        except (ImportError, OSError, ValueError, RuntimeError):
            loaded_model, loaded_manifest = None, None
    transaction_path = Path(transaction_artifact_dir).resolve() if transaction_artifact_dir is not None else None
    try:
        champion = registry.champion("transaction_anomaly")
        if champion:
            transaction_path = Path(registry.get_version("transaction_anomaly", champion["version"])["artifact_path"])
        if transaction_path is None:
            raise FileNotFoundError
        _, tx_manifest = load_transaction_artifacts(transaction_path)
        transaction_model_available = True
    except (ImportError, OSError, ValueError, RuntimeError):
        transaction_model_available = False
    manager = AnalysisManager(loaded_model, loaded_manifest, store, incoming,
        max_input_records=max_input_records, semantic_dictionary=SemanticDictionary(dictionary_path),
        transaction_artifact_dir=transaction_path)
    _restore_owned_results(manager)
    app.state.analysis_manager = manager
    app.state.model_registry = registry
    app.state.model_error = None if loaded_model is not None else "Клиентская модель недоступна."
    app.state.transaction_model_available = transaction_model_available
    app.state.max_input_bytes = int(max_input_bytes)
    app.state.max_input_records = int(max_input_records)
    app.state.analyst_lifespan_active = True
    if loaded_model is not None and model is None:
        registry.bootstrap_champion("client_risk", loaded_manifest.model_version, client_path)
    if transaction_model_available:
        registry.bootstrap_champion("transaction_anomaly", str(tx_manifest["model_version"]), transaction_path)
    try:
        yield
    finally:
        if manager.tasks:
            await asyncio.gather(*tuple(manager.tasks), return_exceptions=True)
        app.state.analyst_lifespan_active = False
