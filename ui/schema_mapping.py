"""Schema mapping screen: review the agent's proposal against the raw data."""

import pandas as pd
import streamlit as st

from core.canonical import field_by_key
from core.config import CONFIDENCE_THRESHOLD, RAW_PREVIEW_ROWS
from core.models import DatasetMapping, FieldMapping
from ingestion.storage import load_dataframe
from mapping.schema_mapping import confirm_mapping, has_mapping, load_artifact
from transform.canonical_table import build_canonical_table
from triage.workbook_triage import load_datasets

UNMAPPED = "-- not mapped --"


def render() -> None:
    st.title("Schema Mapping")
    st.markdown(
        "The agent read each export's column names, types and a few sample values, and "
        "proposed which column carries which canonical field. Check it against the raw "
        "data below and correct anything that is wrong, whatever the confidence says."
    )

    run_id = st.session_state.get("run_id")
    if run_id is None or not has_mapping(run_id):
        st.info("No mapping yet. Upload your ERP exports on the Start page and run the analysis.")
        return

    artifact = load_artifact(run_id)
    if not artifact.datasets:
        st.warning("No sheet was triaged as transactions, so nothing was mapped.")
        return

    if len(artifact.datasets) == 1:
        edits = {artifact.datasets[0].dataset_id: _render_dataset(run_id, artifact.datasets[0])}
    else:
        edits = {}
        for tab, dataset in zip(
            st.tabs([_label(dataset) for dataset in artifact.datasets]), artifact.datasets
        ):
            with tab:
                edits[dataset.dataset_id] = _render_dataset(run_id, dataset)

    st.divider()
    if st.button("Confirm mapping and continue", type="primary"):
        _confirm(run_id, artifact.datasets, edits)


def _render_dataset(run_id: str, dataset: DatasetMapping) -> pd.DataFrame:
    source_columns = [profile.name for profile in dataset.column_profiles]

    edited = st.data_editor(
        pd.DataFrame(
            [
                {
                    "Canonical field": field_by_key(mapping.canonical_field).label,
                    "Source column": mapping.source_column or UNMAPPED,
                    "Confidence": mapping.confidence,
                    "Status": _status(mapping),
                    "Comment": mapping.comment,
                }
                for mapping in dataset.mappings
            ]
        ),
        key=f"editor_{dataset.dataset_id}",
        width="stretch",
        hide_index=True,
        disabled=["Canonical field", "Confidence", "Status", "Comment"],
        column_config={
            "Source column": st.column_config.SelectboxColumn(
                options=[UNMAPPED, *source_columns],
                required=True,
                help="Pick the column from this export that carries the canonical field.",
            ),
            "Confidence": st.column_config.NumberColumn(format="%.2f", width="small"),
            "Status": st.column_config.TextColumn(width="small"),
            "Comment": st.column_config.TextColumn(width="large"),
        },
    )

    st.subheader("Raw data")
    caption = f"First {RAW_PREVIEW_ROWS} rows of {dataset.original_filename}"
    if dataset.sheet:
        caption += f", sheet '{dataset.sheet}'"
    st.caption(caption)
    st.dataframe(_raw_preview(run_id, dataset), width="stretch", hide_index=True)
    return edited


def _confirm(run_id: str, datasets: list[DatasetMapping], edits: dict[str, pd.DataFrame]) -> None:
    selections = {
        dataset.dataset_id: _selections(dataset, edits[dataset.dataset_id])
        for dataset in datasets
    }
    with st.status("Building the canonical table", expanded=True) as status:
        st.write("Confirming the mapping")
        confirm_mapping(run_id, selections)
        st.write("Applying it to the data")
        report = build_canonical_table(run_id)
        status.update(label=f"Canonical table built: {report.row_count:,} rows", state="complete")

    st.session_state["switch_to"] = "canonical_table"
    st.rerun()


def _label(dataset: DatasetMapping) -> str:
    name = dataset.company_label or dataset.original_filename
    return f"{name} - {dataset.sheet}" if dataset.sheet else name


def _status(mapping: FieldMapping) -> str:
    if mapping.decided_by == "user":
        return "Set by you"
    if mapping.source_column is None:
        return "Missing" if field_by_key(mapping.canonical_field).required else "Not mapped"
    return "Review" if mapping.confidence < CONFIDENCE_THRESHOLD else "OK"


def _selections(dataset: DatasetMapping, edited: pd.DataFrame) -> dict[str, str | None]:
    return {
        mapping.canonical_field: (None if value == UNMAPPED else value)
        for mapping, value in zip(dataset.mappings, edited["Source column"])
    }


def _raw_preview(run_id: str, dataset: DatasetMapping) -> pd.DataFrame:
    source = next(d for d in load_datasets(run_id) if d.dataset_id == dataset.dataset_id)
    return load_dataframe(run_id, source).head(RAW_PREVIEW_ROWS)
