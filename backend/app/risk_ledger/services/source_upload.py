from __future__ import annotations

import re
import shutil
import struct
import uuid
from pathlib import Path

from app.risk_ledger.domain.contracts import SourceFormat


SUPPORTED_EXTENSIONS: dict[str, SourceFormat] = {
    ".csv": SourceFormat.CSV,
    ".json": SourceFormat.JSON,
    ".jsonl": SourceFormat.JSONL,
    ".ndjson": SourceFormat.NDJSON,
    ".sql": SourceFormat.SQL_DUMP,
    ".sqlite": SourceFormat.SQLITE,
    ".sqlite3": SourceFormat.SQLITE,
    ".db": SourceFormat.SQLITE,
    ".bson": SourceFormat.BSON,
}

_SQL_DATA_STATEMENT = re.compile(
    rb"\b(CREATE\s+TABLE|INSERT\s+INTO|COPY\s+[^\s]+\s+FROM\s+STDIN)\b",
    re.IGNORECASE,
)


class UploadValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def safe_filename(filename: str | None) -> str:
    normalized = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not normalized or normalized in {".", ".."}:
        raise UploadValidationError("invalid_filename", "File name is missing.")
    if any(ord(character) < 32 for character in normalized):
        raise UploadValidationError(
            "invalid_filename", "File name contains control characters."
        )
    return normalized


def detect_source_format(filename: str) -> SourceFormat:
    extension = Path(filename).suffix.lower()
    try:
        return SUPPORTED_EXTENSIONS[extension]
    except KeyError as error:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise UploadValidationError(
            "unsupported_file",
            f"Unsupported file type. Supported extensions: {supported}.",
        ) from error


def prepare_staged_upload(
    incoming_root: str | Path,
    analysis_id: str,
    filename: str,
) -> Path:
    root = Path(incoming_root).resolve()
    normalized_id = str(uuid.UUID(analysis_id))
    directory = (root / normalized_id).resolve()
    if directory.parent != root:
        raise RuntimeError("Unsafe upload directory.")
    directory.mkdir(parents=False, exist_ok=False)
    extension = Path(filename).suffix.lower()
    return directory / f"source{extension}"


def _is_text_payload(sample: bytes) -> bool:
    if not sample or b"\x00" in sample:
        return False
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            sample.decode(encoding)
            return True
        except UnicodeDecodeError:
            continue
    return False


def validate_staged_signature(path: str | Path, source_format: SourceFormat) -> None:
    source_path = Path(path)
    size = source_path.stat().st_size
    if size == 0:
        raise UploadValidationError("empty_file", "Uploaded file is empty.")

    with source_path.open("rb") as stream:
        sample = stream.read(min(size, 64 * 1024))

    valid = False
    if source_format == SourceFormat.CSV:
        valid = _is_text_payload(sample)
    elif source_format in {SourceFormat.JSON, SourceFormat.JSONL, SourceFormat.NDJSON}:
        valid = _is_text_payload(sample) and sample.lstrip().startswith((b"{", b"["))
    elif source_format == SourceFormat.SQL_DUMP:
        valid = _is_text_payload(sample) and _SQL_DATA_STATEMENT.search(sample) is not None
    elif source_format == SourceFormat.SQLITE:
        valid = sample.startswith(b"SQLite format 3\x00")
    elif source_format == SourceFormat.BSON:
        if len(sample) >= 5:
            document_size = struct.unpack("<i", sample[:4])[0]
            valid = 5 <= document_size <= size
            if valid and document_size <= len(sample):
                valid = sample[document_size - 1] == 0

    if not valid:
        raise UploadValidationError(
            "invalid_file_signature",
            f"File content does not match the {source_format.value} format.",
        )


def staged_directory(incoming_root: str | Path, analysis_id: str) -> Path:
    root = Path(incoming_root).resolve()
    normalized_id = str(uuid.UUID(analysis_id))
    directory = (root / normalized_id).resolve()
    if directory.parent != root:
        raise RuntimeError("Unsafe upload directory.")
    return directory


def remove_staged_upload(incoming_root: str | Path, analysis_id: str) -> None:
    directory = staged_directory(incoming_root, analysis_id)
    if directory.is_dir():
        shutil.rmtree(directory)


def cleanup_staging_root(incoming_root: str | Path) -> list[str]:
    root = Path(incoming_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    removed: list[str] = []
    for path in root.iterdir():
        try:
            analysis_id = str(uuid.UUID(path.name))
        except ValueError:
            continue
        if path.is_dir():
            remove_staged_upload(root, analysis_id)
            removed.append(analysis_id)
        elif path.is_file():
            path.unlink(missing_ok=True)
            removed.append(analysis_id)
    return removed
