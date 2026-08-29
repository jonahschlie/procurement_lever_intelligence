"""Persisted metadata describing an uploaded ERP export."""

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
    upload_id: str
    original_filename: str
    stored_filename: str
    content_hash: str
    size_bytes: int
    uploaded_at: datetime
    company_label: str | None
    file_format: FileFormat
    read_options: ReadOptions
    row_count: int
    column_names: list[str]
