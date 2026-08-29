"""Persist uploaded ERP exports byte-identically together with their metadata.

Layout per upload::

    data/uploads/<upload_id>/
        source.csv | source.xlsx   # untouched original bytes
        manifest.json              # UploadManifest

Keeping the original bytes plus a content hash is what makes the downstream
pipeline auditable: every derived figure can be traced back to the exact export
it came from.
"""

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from core.config import uploads_dir
from core.models import UploadManifest
from ingestion.readers import file_format, read_tabular, read_with_options

MANIFEST_NAME = "manifest.json"


def save_upload(
    data: bytes,
    filename: str,
    company_label: str | None = None,
    sheet: str | None = None,
) -> tuple[UploadManifest, bool]:
    """Store a file and its manifest.

    Returns the manifest and whether the content was already present. Identical
    content is never stored twice -- the existing manifest is returned instead.
    """
    content_hash = hashlib.sha256(data).hexdigest()
    duplicate = _find_by_hash(content_hash)
    if duplicate is not None:
        return duplicate, True

    fmt = file_format(filename)
    frame, read_options = read_tabular(data, filename, sheet)
    uploaded_at = datetime.now(timezone.utc)
    upload_id = f"{uploaded_at:%Y%m%dT%H%M%S}-{content_hash[:8]}"
    stored_filename = f"source.{fmt}"

    manifest = UploadManifest(
        upload_id=upload_id,
        original_filename=filename,
        stored_filename=stored_filename,
        content_hash=content_hash,
        size_bytes=len(data),
        uploaded_at=uploaded_at,
        company_label=company_label or None,
        file_format=fmt,
        read_options=read_options,
        row_count=len(frame),
        column_names=list(frame.columns),
    )

    target = uploads_dir() / upload_id
    target.mkdir(parents=True, exist_ok=True)
    (target / stored_filename).write_bytes(data)
    (target / MANIFEST_NAME).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest, False


def list_uploads() -> list[UploadManifest]:
    """All stored uploads, newest first."""
    root = uploads_dir()
    if not root.is_dir():
        return []
    manifests = [
        UploadManifest.model_validate_json(path.read_text(encoding="utf-8"))
        for path in root.glob(f"*/{MANIFEST_NAME}")
    ]
    # uploaded_at rather than upload_id: the id carries only second precision.
    return sorted(manifests, key=lambda manifest: manifest.uploaded_at, reverse=True)


def load_manifest(upload_id: str) -> UploadManifest:
    path = _upload_path(upload_id) / MANIFEST_NAME
    return UploadManifest.model_validate_json(path.read_text(encoding="utf-8"))


def load_dataframe(upload_id: str) -> pd.DataFrame:
    """Re-read a stored upload using the options recorded at upload time."""
    manifest = load_manifest(upload_id)
    data = (_upload_path(upload_id) / manifest.stored_filename).read_bytes()
    return read_with_options(data, manifest.file_format, manifest.read_options)


def delete_upload(upload_id: str) -> None:
    shutil.rmtree(_upload_path(upload_id))


def _upload_path(upload_id: str) -> Path:
    path = uploads_dir() / upload_id
    if not path.is_dir():
        raise FileNotFoundError(f"unknown upload {upload_id!r}")
    return path


def _find_by_hash(content_hash: str) -> UploadManifest | None:
    return next((m for m in list_uploads() if m.content_hash == content_hash), None)
