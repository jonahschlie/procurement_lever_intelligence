"""Canonicalization step: turn the confirmed mapping into one working table.

Up to here the mapping is a description. This step applies it once and
materialises the result, so that from now on the pipeline reads canonical column
names and nothing has to re-derive them -- the point at which SYSTEMCONCEPT
section 5 says the pipeline becomes ERP-independent.

Two things this step deliberately does not do:

Values are renamed, never converted. ``1.250,00`` stays the string ``1.250,00``.
Typing, normalization and currency conversion belong to the rule engine
(section 7) and section 9; doing any of it here would reinterpret data before the
deterministic rules have had their say.

The schema is complete even where nothing was mapped. All canonical columns
always exist, unmapped ones empty, so nothing downstream has to test whether a
column is present.
"""

from logging import Logger

import pandas as pd

from core.canonical import CANONICAL_FIELDS
from core.models import (
    CanonicalTableReport,
    Dataset,
    DatasetContribution,
    DatasetMapping,
)
from core.run import get_logger, record_step, step_path
from core.table import write_table
from ingestion.storage import load_dataframe
from mapping.schema_mapping import load_confirmed
from triage.workbook_triage import load_datasets

STEP = "canonical_table"
ARTIFACT_NAME = "canonicalization.json"

PROVENANCE_COLUMNS = (
    "dataset_id",
    "source_file",
    "source_sheet",
    "source_row",
    "company_label",
)
CANONICAL_COLUMNS = tuple(field.key for field in CANONICAL_FIELDS)
RESOLVED_COLUMNS = ("company_source",)

COLUMN_ORDER = PROVENANCE_COLUMNS + CANONICAL_COLUMNS + RESOLVED_COLUMNS


def build_canonical_table(run_id: str) -> CanonicalTableReport:
    logger = get_logger(run_id)
    datasets = {dataset.dataset_id: dataset for dataset in load_datasets(run_id)}

    frames, contributions = [], []
    for mapping in load_confirmed(run_id).datasets:
        frame, contribution = _canonicalize(run_id, datasets[mapping.dataset_id], mapping)
        frames.append(frame)
        contributions.append(contribution)
        logger.info(
            "canonicalized %s: %d rows, %d of %d fields mapped",
            mapping.dataset_id,
            contribution.row_count,
            len(contribution.mapped_fields),
            len(CANONICAL_FIELDS),
        )

    table = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame({column: pd.Series(dtype=str) for column in COLUMN_ORDER})
    )
    write_table(run_id, table, STEP, note="built from the confirmed schema mapping")

    report = CanonicalTableReport(
        row_count=len(table),
        column_names=list(COLUMN_ORDER),
        contributions=contributions,
    )
    target = step_path(run_id, STEP)
    path = target / ARTIFACT_NAME
    path.write_bytes(report.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [path])
    logger.info(
        "canonical table built: %d rows from %d dataset(s)", len(table), len(contributions)
    )
    return report


def _canonicalize(
    run_id: str, dataset: Dataset, mapping: DatasetMapping
) -> tuple[pd.DataFrame, DatasetContribution]:
    source = load_dataframe(run_id, dataset)
    chosen = {
        entry.canonical_field: entry.source_column
        for entry in mapping.mappings
        if entry.source_column is not None
    }

    canonical = pd.DataFrame(index=source.index)
    for field in CANONICAL_FIELDS:
        column = chosen.get(field.key)
        canonical[field.key] = (
            source[column].astype(str) if column in source.columns else ""
        )

    company, company_source = _resolve_company(canonical["company"], dataset.company_label)
    canonical["company"] = company
    canonical["company_source"] = company_source

    provenance = pd.DataFrame(
        {
            "dataset_id": dataset.dataset_id,
            "source_file": dataset.original_filename,
            "source_sheet": dataset.sheet or "",
            # 1-based, and offset by the header row, so it points at the row a
            # reviewer would find when opening the source file.
            "source_row": [str(position + 2) for position in range(len(source))],
            "company_label": dataset.company_label or "",
        },
        index=source.index,
    )

    frame = pd.concat([provenance, canonical], axis=1)[list(COLUMN_ORDER)]
    contribution = DatasetContribution(
        dataset_id=dataset.dataset_id,
        original_filename=dataset.original_filename,
        company_label=dataset.company_label,
        sheet=dataset.sheet,
        row_count=len(frame),
        mapped_fields=[field.key for field in CANONICAL_FIELDS if field.key in chosen],
        unmapped_fields=[field.key for field in CANONICAL_FIELDS if field.key not in chosen],
        company_source_counts={
            value: int(count) for value, count in company_source.value_counts().items()
        },
    )
    return frame, contribution


def _resolve_company(
    mapped: pd.Series, label: str | None
) -> tuple[pd.Series, pd.Series]:
    """A company column in the data wins; the name given at upload is the fallback.

    What is left over is genuinely missing, and section 7 flags it as such.
    """
    values = mapped.astype(str).str.strip()
    present = values != ""

    resolved = values.where(present, label or "")
    source = pd.Series("data", index=values.index).where(
        present, "upload_label" if label else "missing"
    )
    return resolved, source


def load_report(run_id: str) -> CanonicalTableReport:
    path = step_path(run_id, STEP) / ARTIFACT_NAME
    return CanonicalTableReport.model_validate_json(path.read_bytes())
