"""Workbook review: confirm what each sheet is before anything is analysed."""

import pandas as pd
import streamlit as st

from core.models import SHEET_ROLES, WorkbookTriage
from mapping.schema_mapping import run_schema_mapping
from triage.workbook_triage import (
    confirm_triage,
    has_confirmed,
    has_triage,
    load_confirmed_triage,
    load_triage,
)
from ui.sidebar import render_run_sidebar

ROLE_HELP = {
    "transactions": "The spend data itself. This is what gets mapped and analysed.",
    "fx_rates": "Currency conversion table. Kept for currency harmonization.",
    "supplier_master": "Supplier list. Kept for supplier normalization.",
    "documentation": "Cover letter, instructions, glossary. Not analysed.",
    "unknown": "Could not be placed. Pick a role or leave it out of the analysis.",
}


def render() -> None:
    render_run_sidebar()

    st.title("Workbook Review")
    st.markdown(
        "Submission workbooks usually hold more than a table: a cover letter, filling "
        "instructions, the transactions, and small lookup tables. Shape decides what is "
        "a table at all; the agent decides what each table is for. Correct anything "
        "that is wrong before the analysis runs."
    )

    run_id = st.session_state.get("run_id")
    if run_id is None or not has_triage(run_id):
        st.info("Nothing to review yet. Upload your ERP exports on the Start page.")
        return

    artifact = load_confirmed_triage(run_id) if has_confirmed(run_id) else load_triage(run_id)

    edits = {
        workbook.stored_filename: _render_workbook(workbook) for workbook in artifact.workbooks
    }

    st.divider()
    if st.button("Confirm and continue", type="primary"):
        _confirm(run_id, edits)


def _render_workbook(workbook: WorkbookTriage) -> pd.DataFrame:
    st.subheader(workbook.original_filename)
    if workbook.llm_call is None:
        st.caption("Only one table in this file, so no agent was needed.")

    profiles = {profile.name: profile for profile in workbook.sheets}
    return st.data_editor(
        pd.DataFrame(
            [
                {
                    "Sheet": entry.sheet or "(single table)",
                    "Rows": profiles[entry.sheet].rows,
                    "Columns": profiles[entry.sheet].columns,
                    "Role": entry.role,
                    "Confidence": entry.confidence,
                    "Comment": entry.comment,
                }
                for entry in workbook.classifications
            ]
        ),
        key=f"triage_{workbook.stored_filename}",
        width="stretch",
        hide_index=True,
        disabled=["Sheet", "Rows", "Columns", "Confidence", "Comment"],
        column_config={
            "Role": st.column_config.SelectboxColumn(
                options=list(SHEET_ROLES),
                required=True,
                help=" | ".join(f"{role}: {text}" for role, text in ROLE_HELP.items()),
            ),
            "Confidence": st.column_config.NumberColumn(format="%.2f", width="small"),
            "Comment": st.column_config.TextColumn(width="large"),
        },
    )


def _confirm(run_id: str, edits: dict[str, pd.DataFrame]) -> None:
    roles = {
        stored_filename: dict(zip(table["Sheet"], table["Role"]))
        for stored_filename, table in edits.items()
    }
    with st.status("Running analysis", expanded=True) as status:
        st.write("Confirming sheet roles")
        confirmed = confirm_triage(run_id, roles)

        transactional = [d for d in confirmed.datasets if d.role == "transactions"]
        if not transactional:
            status.update(label="No transaction sheet selected", state="error")
            st.warning(
                "No sheet is marked as transactions, so there is nothing to map. "
                "Set the role of the sheet holding the spend data and confirm again."
            )
            return

        st.write(f"Mapping {len(transactional)} dataset(s) onto the canonical schema")
        run_schema_mapping(run_id)
        status.update(label="Analysis complete", state="complete")

    st.session_state["switch_to"] = "schema_mapping"
    st.rerun()
