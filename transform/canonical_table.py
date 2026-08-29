"""Canonicalization step: turn the confirmed mapping into one working table.

Up to here the mapping is a description. This step applies it once and
materialises the result, so that from now on the pipeline reads canonical column
names and nothing has to re-derive them -- the point at which SYSTEMCONCEPT
section 8 says the pipeline becomes ERP-independent.

Two things this step deliberately does not do:

Values are renamed, never converted. ``1.250,00`` stays the string ``1.250,00``.
Typing, normalization and currency conversion belong to the rule engine
(section 11) and section 13; doing any of it here would reinterpret data before the
deterministic rules have had their say.

The schema is complete even where nothing was mapped. All canonical columns
always exist, unmapped ones empty, so nothing downstream has to test whether a
column is present.

Source columns the mapping did not claim are carried along under an ``extra_``
prefix rather than dropped. They are frequently the ones that explain a
discrepancy later -- a document type that says why a subtotal disagrees with the
detail -- and going back to the source file to fetch them would defeat the point
of having a working table.
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

PROVENANCE_COLUMNS = ("dataset_id", "source_file", "source_sheet", "source_row")
CANONICAL_COLUMNS = tuple(field.key for field in CANONICAL_FIELDS)
BASE_COLUMNS = PROVENANCE_COLUMNS + CANONICAL_COLUMNS

EXTRA_PREFIX = "extra_"


def build_canonical_table(run_id: str) -> CanonicalTableReport:
    logger = get_logger(run_id)
    datasets = {dataset.dataset_id: dataset for dataset in load_datasets(run_id)}

    frames, contributions = [], []
    for mapping in load_confirmed(run_id).datasets:
        frame, contribution = _canonicalize(run_id, datasets[mapping.dataset_id], mapping)
        frames.append(frame)
        contributions.append(contribution)
        logger.info(
            "canonicalized %s: %d rows, %d of %d fields mapped, %d spare column(s) kept",
            mapping.dataset_id,
            contribution.row_count,
            len(contribution.mapped_fields),
            len(CANONICAL_FIELDS),
            len(contribution.extra_columns),
        )

    table = _stack(frames)
    write_table(run_id, table, STEP, note="built from the confirmed schema mapping")

    report = CanonicalTableReport(
        row_count=len(table),
        column_names=[str(column) for column in table.columns],
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
    claimed = set(chosen.values())
    spare = [str(column) for column in source.columns if str(column) not in claimed]

    canonical = pd.DataFrame(
        {
            field.key: (
                source[chosen[field.key]].astype(str)
                if chosen.get(field.key) in source.columns
                else ""
            )
            for field in CANONICAL_FIELDS
        },
        index=source.index,
    )
    provenance = pd.DataFrame(
        {
            "dataset_id": dataset.dataset_id,
            "source_file": dataset.original_filename,
            "source_sheet": dataset.sheet or "",
            # 1-based, and offset by the header row, so it points at the row a
            # reviewer would find when opening the source file.
            "source_row": [str(position + 2) for position in range(len(source))],
        },
        index=source.index,
    )
    extras = pd.DataFrame(
        {f"{EXTRA_PREFIX}{column}": source[column].astype(str) for column in spare},
        index=source.index,
    )

    frame = pd.concat([provenance, canonical, extras], axis=1)
    contribution = DatasetContribution(
        dataset_id=dataset.dataset_id,
        original_filename=dataset.original_filename,
        sheet=dataset.sheet,
        row_count=len(frame),
        mapped_fields=[field.key for field in CANONICAL_FIELDS if field.key in chosen],
        unmapped_fields=[field.key for field in CANONICAL_FIELDS if field.key not in chosen],
        extra_columns=spare,
    )
    return frame, contribution


def _stack(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Put the datasets under each other, canonical columns first, extras after.

    Exports differ in which spare columns they carry, so a column missing from one
    dataset becomes empty for its rows rather than absent from the table.
    """
    if not frames:
        return pd.DataFrame({column: pd.Series(dtype=str) for column in BASE_COLUMNS})

    table = pd.concat(frames, ignore_index=True)
    extras = [column for column in table.columns if str(column).startswith(EXTRA_PREFIX)]
    return table[list(BASE_COLUMNS) + extras].fillna("").astype(str)


def load_report(run_id: str) -> CanonicalTableReport:
    path = step_path(run_id, STEP) / ARTIFACT_NAME
    return CanonicalTableReport.model_validate_json(path.read_bytes())
