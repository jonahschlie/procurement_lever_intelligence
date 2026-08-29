"""Metadata persisted inside a run workspace."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

FileFormat = Literal["csv", "xlsx"]
DecidedBy = Literal["ai", "user"]


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


class ColumnProfile(BaseModel):
    """What the schema mapping agent is shown about one source column."""

    name: str
    inferred_type: str
    null_ratio: float
    distinct_count: int
    sample_values: list[str]


class FieldMapping(BaseModel):
    """One canonical field and the source column it was matched to."""

    canonical_field: str
    source_column: str | None
    confidence: float
    comment: str
    decided_by: DecidedBy = "ai"


class LlmCall(BaseModel):
    """Audit record of a single model call."""

    model: str
    input_tokens: int
    output_tokens: int
    duration_seconds: float


class DatasetMapping(BaseModel):
    stored_filename: str
    original_filename: str
    sheet: str | None
    column_profiles: list[ColumnProfile]
    mappings: list[FieldMapping]
    llm_call: LlmCall


class SchemaMappingArtifact(BaseModel):
    datasets: list[DatasetMapping]
