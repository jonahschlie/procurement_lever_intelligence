"""Metadata persisted inside a run workspace."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

FileFormat = Literal["csv", "xlsx"]
DecidedBy = Literal["ai", "user"]

# What a sheet is for. Only 'transactions' feeds the schema mapping; fx_rates and
# supplier_master are kept because currency harmonization and supplier
# normalization will need them. 'documentation' yields no dataset at all.
SheetRole = Literal[
    "transactions", "fx_rates", "supplier_master", "documentation", "unknown"
]
SHEET_ROLES: tuple[SheetRole, ...] = (
    "transactions",
    "fx_rates",
    "supplier_master",
    "documentation",
    "unknown",
)


class ReadOptions(BaseModel):
    """Exact parameters used to parse a source file.

    Stored alongside the file so it is re-read identically later instead of
    being sniffed again, which would make the pipeline non-reproducible.
    """

    encoding: str | None = None
    delimiter: str | None = None
    sheet: str | None = None


class FileManifest(BaseModel):
    """One uploaded file as stored by the ingestion step.

    A file is not yet a dataset: a workbook can hold several sheets, and which of
    them carry data is decided by the triage step.
    """

    original_filename: str
    stored_filename: str
    content_hash: str
    size_bytes: int
    company_label: str | None
    file_format: FileFormat
    read_options: ReadOptions
    sheet_names: list[str]


class SheetProfile(BaseModel):
    """Deterministic shape of one sheet, measured without any model."""

    name: str
    rows: int
    columns: int
    fill_ratio: float
    rectangularity: float
    has_header_row: bool
    has_numeric_column: bool
    has_date_column: bool
    looks_like_table: bool
    header: list[str]
    sample_rows: list[list[str]]


class SheetClassification(BaseModel):
    sheet: str
    role: SheetRole
    confidence: float
    comment: str
    decided_by: DecidedBy = "ai"


class Dataset(BaseModel):
    """One sheet worth analysing, with the role it plays."""

    dataset_id: str
    original_filename: str
    stored_filename: str
    sheet: str | None
    role: SheetRole
    company_label: str | None
    read_options: ReadOptions
    row_count: int
    column_names: list[str]


class LlmCall(BaseModel):
    """Audit record of a single model call."""

    model: str
    input_tokens: int
    output_tokens: int
    duration_seconds: float


class WorkbookTriage(BaseModel):
    original_filename: str
    stored_filename: str
    company_label: str | None = None
    sheets: list[SheetProfile]
    classifications: list[SheetClassification]
    llm_call: LlmCall | None


class WorkbookTriageArtifact(BaseModel):
    workbooks: list[WorkbookTriage]
    datasets: list[Dataset] = []


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


class DatasetMapping(BaseModel):
    dataset_id: str
    original_filename: str
    company_label: str | None = None
    sheet: str | None
    column_profiles: list[ColumnProfile]
    mappings: list[FieldMapping]
    llm_call: LlmCall


class SchemaMappingArtifact(BaseModel):
    datasets: list[DatasetMapping]


class StepRecord(BaseModel):
    step: str
    completed_at: datetime
    artifacts: list[str]


class RunManifest(BaseModel):
    """Audit trail of a single run: when it started and what each step produced."""

    run_id: str
    created_at: datetime
    steps: list[StepRecord] = []
