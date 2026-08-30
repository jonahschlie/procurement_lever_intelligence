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
    sheet: str | None
    column_profiles: list[ColumnProfile]
    mappings: list[FieldMapping]
    llm_call: LlmCall


class SchemaMappingArtifact(BaseModel):
    datasets: list[DatasetMapping]


class TableRevision(BaseModel):
    """One step's effect on the working table."""

    step: str
    written_at: datetime
    row_count: int
    columns_added: list[str]
    note: str


class TableMeta(BaseModel):
    """State of the working table, plus how it got there.

    The table is overwritten in place, so this history is what still answers which
    step touched it and what it changed.
    """

    row_count: int
    column_names: list[str]
    revisions: list[TableRevision] = []


class DatasetContribution(BaseModel):
    dataset_id: str
    original_filename: str
    sheet: str | None
    row_count: int
    mapped_fields: list[str]
    unmapped_fields: list[str]
    extra_columns: list[str]


class CanonicalTableReport(BaseModel):
    row_count: int
    column_names: list[str]
    contributions: list[DatasetContribution]


Severity = Literal["high", "medium", "low", "info"]
CheckCategory = Literal[
    "completeness", "consistency", "semantic", "aggregates", "reconciliation", "readiness"
]


class Finding(BaseModel):
    check: str
    category: CheckCategory
    severity: Severity
    result: str
    affected_rows: int
    detail: str
    examples: list[str] = []


class AggregateCandidate(BaseModel):
    """A row that looks like an embedded subtotal rather than a booking."""

    position: int
    source_row: str
    company: str
    label: str
    amount: str
    reasons: list[str]
    exclude: bool = True


class CompanyReconciliation(BaseModel):
    company: str
    detail_total: float
    stated_total: float
    difference: float
    detail_rows: int


class ProfilingReport(BaseModel):
    row_count: int
    findings: list[Finding]
    aggregate_candidates: list[AggregateCandidate]
    reconciliation: list[CompanyReconciliation]
    category_analysis_enabled: bool
    category_decision: str
    value_formats: dict[str, str]


class RuleEffect(BaseModel):
    rule: str
    column: str
    affected_rows: int
    detail: str


class RuleReport(BaseModel):
    row_count: int
    effects: list[RuleEffect]
    spend_before: float
    spend_after: float
    excluded_rows: int
    eligibility: dict[str, int]


class CurrencyBreakdown(BaseModel):
    currency: str
    rows: int
    sum_local: float
    rate_min: float | None
    rate_max: float | None
    sum_eur: float


class CurrencyReport(BaseModel):
    """What the conversion did, and the spend semantics that follow from it.

    Spend counts net: credit notes reduce it, because the net figure is what
    actually flowed and therefore what one negotiates over. Gross and the credit
    volume are carried alongside.
    """

    row_count: int
    rate_source: str
    rates_frozen_to: str
    spend_net_eur: float
    spend_gross_eur: float
    credit_volume_eur: float
    converted_rows: int
    flagged_rows: int
    group_unconverted_rows: int
    breakdown: list[CurrencyBreakdown]


class SupplierGroup(BaseModel):
    """One proposed canonical supplier and the raw names it absorbs."""

    group_id: int
    canonical_name: str
    canonical_id: str
    members: list[str]
    row_count: int
    source: Literal["deterministic", "ai", "ai_unsure", "user"]
    confidence: float
    comment: str
    master_id: str | None = None
    country: str | None = None
    # None where the supplier is not in the master at all: that is "unknown",
    # which is a different statement from "no contract on file".
    contract_on_file: bool | None = None
    # A cluster of the group's own entities: not procurement spend at all.
    is_intercompany: bool = False
    intercompany_reason: str = ""
    approved: bool


class RejectedPair(BaseModel):
    left: str
    right: str
    similarity: float
    comment: str


class SupplierNormalizationArtifact(BaseModel):
    distinct_names: int
    groups: list[SupplierGroup]
    rejected: list[RejectedPair]
    llm_call: LlmCall | None


class CostTypeClass(BaseModel):
    """One cost type and whether procurement can influence it."""

    cost_type: str
    addressable: bool
    confidence: float
    comment: str
    spend: float
    rows: int
    decided_by: DecidedBy = "ai"


class SpendClassificationArtifact(BaseModel):
    source_column: str
    cost_types: list[CostTypeClass]
    llm_call: LlmCall | None


class SpendChainStep(BaseModel):
    label: str
    amount: float
    delta: float | None = None
    note: str = ""


class SpendReport(BaseModel):
    """The chain from what was booked to what procurement can act on."""

    rows_total: int
    rows_analysed: int
    chain: list[SpendChainStep]
    intercompany_rows: int
    intercompany_suppliers: list[str]


EffortLevel = Literal["low", "medium", "high"]
ConfidenceLevel = Literal["low", "medium", "high"]


class LeverContributor(BaseModel):
    supplier: str
    spend: float
    rows: int
    companies: int
    contract_status: str


LeverStatus = Literal["quantified", "not_applicable", "not_assessable"]
LeverKind = Literal["saving", "recovery", "risk"]


class LeverResult(BaseModel):
    """One lever: whether it applies here, what it is worth, and on which rows.

    A zero base means two very different things -- tested and found nothing, or
    could not be tested at all -- so the status carries that distinction rather
    than leaving a reader to guess from a zero.
    """

    lever_id: str
    name: str
    mechanism: str
    status: LeverStatus = "quantified"
    status_reason: str = ""
    kind: LeverKind = "saving"
    required_fields: list[str] = []
    missing_fields: list[str] = []
    metric: str = ""
    gross_base: float
    net_base: float
    rows: int
    suppliers: int
    companies: int
    rate_low: float
    rate_base: float
    rate_high: float
    potential_low: float
    potential_base: float
    potential_high: float
    effort: EffortLevel
    effort_reason: str
    confidence: ConfidenceLevel
    confidence_reason: str
    contributors: list[LeverContributor]
    opportunity: str = ""
    next_steps: list[str] = []


class CompanyBenchmark(BaseModel):
    company: str
    spend: float
    suppliers: int
    po_coverage: float
    uncontracted_share: float


class DataRequest(BaseModel):
    """A canonical field worth asking the portfolio company for, and why."""

    field: str
    label: str
    unlocks: list[str]


class LeverArtifact(BaseModel):
    addressable_spend: float
    levers: list[LeverResult]
    data_requests: list[DataRequest] = []
    total_low: float
    total_base: float
    total_high: float
    benchmark: list[CompanyBenchmark]
    priority_rationale: str = ""
    agent_order: list[str] = []
    agent_order_reason: str = ""
    llm_call: LlmCall | None = None


class StepRecord(BaseModel):
    step: str
    completed_at: datetime
    artifacts: list[str]


class RunManifest(BaseModel):
    """Audit trail of a single run: when it started and what each step produced."""

    run_id: str
    created_at: datetime
    steps: list[StepRecord] = []
