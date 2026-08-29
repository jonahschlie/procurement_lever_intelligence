"""The run's working table.

From the canonicalization step onwards the pipeline works on one table in the
canonical schema. Later steps add quality flags to it rather than writing tables
of their own, so that every step finds the current state in one place.

It lives at the run root rather than inside a numbered step directory, because it
belongs to no single step -- it is the run's evolving state. Each step still
writes its own report into its own directory.

The table is overwritten in place. Rows are never removed from it (SYSTEMCONCEPT
section 8), so a rewrite only ever adds columns; the revision history in the
metadata records what each step changed, which is what in-place writing would
otherwise cost.
"""

from datetime import datetime, timezone

import pandas as pd

from core.models import TableMeta, TableRevision
from core.run import run_path

TABLE_NAME = "canonical_table.parquet"
META_NAME = "canonical_table.json"


def write_table(run_id: str, frame: pd.DataFrame, step: str, note: str = "") -> TableMeta:
    columns = [str(column) for column in frame.columns]
    previous = load_table_meta(run_id) if has_table(run_id) else None
    known = set(previous.column_names) if previous else set()

    meta = TableMeta(
        row_count=len(frame),
        column_names=columns,
        revisions=(previous.revisions if previous else [])
        + [
            TableRevision(
                step=step,
                written_at=datetime.now(timezone.utc),
                row_count=len(frame),
                columns_added=[column for column in columns if column not in known],
                note=note,
            )
        ],
    )

    frame.to_parquet(_table_path(run_id), index=False)
    _meta_path(run_id).write_text(meta.model_dump_json(indent=2), encoding="utf-8")
    return meta


def load_table(run_id: str) -> pd.DataFrame:
    return pd.read_parquet(_table_path(run_id))


def load_table_meta(run_id: str) -> TableMeta:
    return TableMeta.model_validate_json(_meta_path(run_id).read_text(encoding="utf-8"))


def has_table(run_id: str) -> bool:
    return _table_path(run_id).is_file()


def _table_path(run_id: str):
    return run_path(run_id) / TABLE_NAME


def _meta_path(run_id: str):
    return run_path(run_id) / META_NAME
