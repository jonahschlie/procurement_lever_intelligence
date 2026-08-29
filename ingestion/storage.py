"""Ingestion step: store uploaded ERP exports inside a run workspace.

Original bytes are written untouched next to a manifest recording how each file
was parsed. Together with the content hash this is what lets any downstream
figure be traced back to the exact export it came from.
"""

import hashlib
from dataclasses import dataclass
from logging import Logger
from pathlib import Path

import pandas as pd
from pydantic import TypeAdapter

from core.models import UploadManifest
from core.run import get_logger, record_step, step_path
from ingestion.readers import file_format, read_tabular, read_with_options

STEP = "ingestion"
ARTIFACT_NAME = "ingestion.json"

_MANIFESTS = TypeAdapter(list[UploadManifest])


@dataclass(frozen=True)
class StagedUpload:
    data: bytes
    filename: str
    company_label: str | None = None
    sheet: str | None = None


def store_uploads(run_id: str, items: list[StagedUpload]) -> list[UploadManifest]:
    """Write every staged export plus the step artifact into the run."""
    target = step_path(run_id, STEP)
    logger = get_logger(run_id)

    manifests = [
        _store_one(target, index, item, logger) for index, item in enumerate(items, start=1)
    ]

    artifact = target / ARTIFACT_NAME
    artifact.write_bytes(_MANIFESTS.dump_json(manifests, indent=2))
    record_step(run_id, STEP, [target / m.stored_filename for m in manifests] + [artifact])
    logger.info("ingestion complete: %d file(s)", len(manifests))
    return manifests


def load_manifests(run_id: str) -> list[UploadManifest]:
    artifact = step_path(run_id, STEP) / ARTIFACT_NAME
    return _MANIFESTS.validate_json(artifact.read_bytes())


def load_dataframe(run_id: str, manifest: UploadManifest) -> pd.DataFrame:
    """Re-read a stored export using the options recorded when it was ingested."""
    data = (step_path(run_id, STEP) / manifest.stored_filename).read_bytes()
    return read_with_options(data, manifest.file_format, manifest.read_options)


def _store_one(target: Path, index: int, item: StagedUpload, logger: Logger) -> UploadManifest:
    frame, read_options = read_tabular(item.data, item.filename, item.sheet)
    # The running prefix keeps identically named exports from overwriting each other.
    name = Path(item.filename).name
    stored_filename = f"{index:02d}_{name}"
    (target / stored_filename).write_bytes(item.data)

    manifest = UploadManifest(
        original_filename=name,
        stored_filename=stored_filename,
        content_hash=hashlib.sha256(item.data).hexdigest(),
        size_bytes=len(item.data),
        company_label=item.company_label or None,
        file_format=file_format(item.filename),
        read_options=read_options,
        row_count=len(frame),
        column_names=list(frame.columns),
    )
    logger.info(
        "stored %s as %s (%d rows, %d columns)",
        name,
        stored_filename,
        manifest.row_count,
        len(manifest.column_names),
    )
    return manifest
