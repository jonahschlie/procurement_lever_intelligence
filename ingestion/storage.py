"""Ingestion step: store uploaded files byte-identically with their metadata.

A file is not yet a dataset. A workbook can hold a cover letter, instructions and
several tables, and deciding which of them carry data is the triage step's job.
This step only preserves the bytes and records how the file is to be read.

Original bytes plus a content hash are what let any downstream figure be traced
back to the exact export it came from.

Layout::

    01_ingestion/
        01_helios.xlsx    # untouched original bytes
        ingestion.json    # one FileManifest per file
"""

import hashlib
from logging import Logger
from pathlib import Path

import pandas as pd
from pydantic import TypeAdapter

from core.models import Dataset, FileManifest
from core.run import get_logger, record_step, step_path
from ingestion.readers import file_format, file_options, list_sheets, read_with_options

STEP = "ingestion"
ARTIFACT_NAME = "ingestion.json"

_MANIFESTS = TypeAdapter(list[FileManifest])


class StagedUpload:
    """A file waiting to be stored, as handed over by the UI."""

    __slots__ = ("data", "filename", "company_label")

    def __init__(self, data: bytes, filename: str, company_label: str | None = None):
        self.data = data
        self.filename = filename
        self.company_label = company_label


def store_files(run_id: str, items: list[StagedUpload]) -> list[FileManifest]:
    """Write every staged file plus the step artifact into the run."""
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


def load_file_manifests(run_id: str) -> list[FileManifest]:
    return _MANIFESTS.validate_json((step_path(run_id, STEP) / ARTIFACT_NAME).read_bytes())


def read_source(run_id: str, stored_filename: str) -> bytes:
    return (step_path(run_id, STEP) / stored_filename).read_bytes()


def load_dataframe(run_id: str, dataset: Dataset) -> pd.DataFrame:
    """Re-read a dataset using the options recorded when it was ingested."""
    data = read_source(run_id, dataset.stored_filename)
    fmt = file_format(dataset.original_filename)
    return read_with_options(data, fmt, dataset.read_options)


def _store_one(target: Path, index: int, item: StagedUpload, logger: Logger) -> FileManifest:
    fmt = file_format(item.filename)
    # The running prefix keeps identically named exports from overwriting each other.
    name = Path(item.filename).name
    stored_filename = f"{index:02d}_{name}"
    (target / stored_filename).write_bytes(item.data)

    manifest = FileManifest(
        original_filename=name,
        stored_filename=stored_filename,
        content_hash=hashlib.sha256(item.data).hexdigest(),
        size_bytes=len(item.data),
        company_label=item.company_label or None,
        file_format=fmt,
        read_options=file_options(item.data, fmt),
        sheet_names=list_sheets(item.data) if fmt == "xlsx" else [],
    )
    logger.info(
        "stored %s as %s (%s)",
        name,
        stored_filename,
        f"{len(manifest.sheet_names)} sheet(s)" if manifest.sheet_names else "single table",
    )
    return manifest
