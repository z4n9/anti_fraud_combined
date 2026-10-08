from __future__ import annotations

from pathlib import Path
import tempfile
import os


TARGET_COLUMN = "GB_flag"
MODEL_VERSION = "2.0.0"
DEFAULT_RANDOM_SEED = 42
DEFAULT_TEST_SIZE = 0.30
DEFAULT_ITERATIONS = 300

MAX_INPUT_BYTES = 500 * 1024 * 1024
MAX_INPUT_RECORDS = 1_000_000
CHUNK_SIZE = 5_000
PREDICTION_BATCH_SIZE = 2_000
SHAP_FACTOR_COUNT = 5
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500
SESSION_TTL_SECONDS = 24 * 60 * 60
MAX_OPTIONAL_MISSING_RATIO = 0.20
DRIFT_MISSING_RATE_DELTA = 0.20
MISSING_CATEGORY = "__MISSING__"
UNKNOWN_CATEGORY = "__UNKNOWN__"

MISSING_TOKENS = frozenset({"", "-", "–", "—", "nan", "none", "null", "n/a"})

CATEGORICAL_FEATURES = frozenset(
    {
        "was_canceled",
        "GENDER",
        "CLASSIFICATION",
        "RESIDENCY",
        "EDUCATION",
        "MARITALSTATUS",
        "NEGATIVESTATUS",
        "PROFESSION",
        "ECONOMYACTIVITYGROUP",
        "EMPLOYMENTNATURE",
    }
)

IDENTIFIER_COLUMNS = frozenset(
    {
        "record_id",
        "client_id",
        "account_id",
        "customer_id",
        "subject_id",
        "iin",
    }
)

ZERO_MISSING_PREFIXES = (
    "NUM_",
    "CNT_",
    "MONTH_OVERDUE_C",
    "MONTH_OVERDUE_A",
)
ZERO_MISSING_COLUMNS = frozenset(
    {
        "overdueinstalmentcount_po_subektu",
        "loans",
        "frequency",
    }
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ARTIFACT_ROOT = PROJECT_ROOT / "artifacts" / "risk_ledger"
DEFAULT_ARTIFACT_DIR = ARTIFACT_ROOT / "current"
DEFAULT_TRANSACTION_ARTIFACT_DIR = (
    ARTIFACT_ROOT / "transaction" / "current"
)
DEFAULT_DICTIONARY_PATH = (
    Path(os.getenv("AMAN_ANALYST_DICTIONARY_PATH", str(ARTIFACT_ROOT / "semantic_dictionary.json")))
)
DEFAULT_SESSION_DIR = Path(
    os.getenv("AMAN_ANALYST_SESSION_DIR", str(PROJECT_ROOT / "runtime" / "risk_ledger" / "sessions"))
)
DEFAULT_MODEL_REGISTRY_DIR = Path(
    os.getenv("AMAN_ANALYST_REGISTRY_DIR", str(PROJECT_ROOT / "runtime" / "risk_ledger" / "model-registry"))
)
