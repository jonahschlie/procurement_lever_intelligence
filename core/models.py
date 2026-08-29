"""Metadata persisted inside a run workspace."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

FileFormat = Literal["csv", "xlsx"]


class ReadOptions(BaseModel):
    """Exact parameters used to parse a source file.

    Stored alongside the file so it is re-read identically later instead of
    being sniffed again, which would make the pipeline non-reproducible.
    """

    encoding: str | None = None
    delimiter: str | None = None
    sheet: str | None = None


class UploadManifest(BaseModel):
    """One ERP export as ingested into a run."""

    original_filename: str
    stored_filename: str
    content_hash: str
    size_bytes: int
    company_label: str | None
    file_format: FileFormat
    read_options: ReadOptions
    row_count: int
    column_names: list[str]


class StepRecord(BaseModel):
    step: str
    completed_at: datetime
    artifacts: list[str]


class RunManifest(BaseModel):
    """Audit trail of a single run: when it started and what each step produced."""

    run_id: str
    created_at: datetime
    steps: list[StepRecord] = []
