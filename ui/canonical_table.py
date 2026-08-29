"""Canonical table screen: what the mapping produced, and what is still empty."""

import pandas as pd
import streamlit as st

from core.canonical import field_by_key
from core.config import PREVIEW_ROWS
from core.run import run_path
from core.table import has_table, load_table, load_table_meta
from transform.canonical_table import load_report


def render() -> None:
    st.title("Canonical Table")
    st.markdown(
        "Every export now speaks the same schema. Values are untouched -- this step "
        "renamed columns and stacked the datasets, it converted nothing. The steps that "
        "follow add quality flags to this table rather than replacing it."
    )

    run_id = st.session_state.get("run_id")
    if run_id is None or not has_table(run_id):
        st.info("No canonical table yet. Confirm the schema mapping to build one.")
        return

    report = load_report(run_id)
    meta = load_table_meta(run_id)

    left, middle, right = st.columns(3)
    left.metric("Rows", f"{report.row_count:,}")
    middle.metric("Columns", len(meta.column_names))
    right.metric("Datasets", len(report.contributions))

    st.subheader("Where the rows came from")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Source": c.original_filename,
                    "Sheet": c.sheet or "-",
                    "Rows": c.row_count,
                    "Mapped fields": len(c.mapped_fields),
                    "Spare columns kept": len(c.extra_columns),
                }
                for c in report.contributions
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    unmapped = sorted(
        {field for c in report.contributions for field in c.unmapped_fields}
    )
    if unmapped:
        st.caption(
            "Canonical fields left empty in at least one dataset: "
            + ", ".join(field_by_key(key).label for key in unmapped)
        )

    spare = sorted({column for c in report.contributions for column in c.extra_columns})
    if spare:
        st.caption(
            "Source columns kept alongside the schema under an 'extra_' prefix: "
            + ", ".join(spare)
        )

    st.subheader("Preview")
    st.dataframe(load_table(run_id).head(PREVIEW_ROWS), width="stretch", hide_index=True)
    st.caption(f"Stored at {run_path(run_id) / 'canonical_table.parquet'}")
