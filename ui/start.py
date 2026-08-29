"""Start screen: what the platform does, then upload and launch the analysis."""

import pandas as pd
import streamlit as st

from agents.client import api_key_configured
from core.config import ALLOWED_EXTENSIONS, PREVIEW_ROWS
from core.models import ReadOptions, SheetProfile
from core.run import create_run
from ingestion.readers import file_format, file_options, read_tabular
from ingestion.sheet_profile import best_table_sheet, profile_sheets
from ingestion.storage import StagedUpload, store_files
from mapping.schema_mapping import run_schema_mapping
from triage.workbook_triage import confirm_triage, needs_review, run_workbook_triage
from ui.sidebar import render_run_sidebar


@st.cache_data(show_spinner=False)
def _parse(data: bytes, filename: str, sheet: str | None) -> tuple[pd.DataFrame, ReadOptions]:
    return read_tabular(data, filename, sheet)


@st.cache_data(show_spinner=False)
def _profiles(data: bytes, filename: str) -> list[SheetProfile]:
    fmt = file_format(filename)
    return profile_sheets(data, fmt, file_options(data, fmt))


def render() -> None:
    render_run_sidebar()

    st.title("Procurement Lever Intelligence")
    st.markdown(
        "Welcome. This platform turns the ERP exports of portfolio companies into one "
        "standardized procurement data model, and uses it to surface value creation "
        "levers across the portfolio: supplier consolidation, category bundling, tail "
        "spend reduction and contract optimization.\n\n"
        "Anything that can be calculated is calculated. Spend figures, aggregations and "
        "quality checks never pass through a language model. AI is used only where "
        "meaning has to be interpreted, beginning with the steps below, which work out "
        "which sheets hold data and translate their columns into the canonical "
        "procurement schema."
    )
    st.divider()

    st.subheader("Upload ERP exports")
    st.caption(
        "One export per portfolio company. Files are stored unchanged and every value is "
        "read as text, so nothing is reinterpreted before the rule engine runs."
    )

    _render_start_results()

    upload_round = st.session_state.get("upload_round", 0)
    files = st.file_uploader(
        "CSV or Excel export",
        type=list(ALLOWED_EXTENSIONS),
        accept_multiple_files=True,
        key=f"uploader_{upload_round}",
    )

    if not files:
        return

    staged = [_stage_file(file) for file in files]
    readable = [item for item in staged if item.get("frame") is not None]
    if st.button("Start analysis", type="primary", disabled=not readable):
        _start_analysis(readable, upload_round)


def _stage_file(file) -> dict:
    data = file.getvalue()
    with st.expander(file.name, expanded=True):
        company = st.text_input(
            "Portfolio company (optional)",
            key=f"company_{file.file_id}",
            help="Fallback only. A company column inside the export takes precedence.",
        )
        try:
            profiles = _profiles(data, file.name)
            # Preview the sheet that looks like data, not simply the first one: in a
            # submission workbook the first sheet is usually the cover letter.
            sheet = best_table_sheet(profiles)
            frame, options = _parse(data, file.name, sheet)
        except Exception as error:
            # Batch upload: one unreadable file must not block the others, so the
            # failure is surfaced here instead of propagating.
            st.error(f"Could not read this file: {error}")
            return {"file": file}

        st.caption(_describe(frame, options, profiles))
        st.dataframe(frame.head(PREVIEW_ROWS), width="stretch")
        return {"file": file, "data": data, "company": company, "frame": frame}


def _describe(frame: pd.DataFrame, options: ReadOptions, profiles: list[SheetProfile]) -> str:
    parts = [f"{len(frame):,} rows x {len(frame.columns)} columns"]
    if options.delimiter:
        parts.append(f"delimiter {options.delimiter!r}")
    if options.encoding:
        parts.append(f"encoding {options.encoding}")
    if len(profiles) > 1:
        parts.append(f"showing sheet '{options.sheet}' of {len(profiles)}")
    return "  |  ".join(parts)


def _start_analysis(staged: list[dict], upload_round: int) -> None:
    if not api_key_configured():
        st.error(
            "OPENAI_API_KEY is not set, so the agents cannot run. Copy .env.example to "
            ".env and add your key, then restart the app."
        )
        return

    run_id = st.session_state.get("run_id") or create_run().run_id
    st.session_state["run_id"] = run_id
    items = [StagedUpload(item["data"], item["file"].name, item["company"]) for item in staged]

    try:
        with st.status("Running analysis", expanded=True) as status:
            st.write(f"Storing {len(items)} file(s) in {run_id}")
            store_files(run_id, items)

            st.write("Working out which sheets hold data")
            triage = run_workbook_triage(run_id)

            if needs_review(triage):
                status.update(label="Sheets identified", state="complete")
                target = "workbook_review"
            else:
                confirm_triage(run_id)
                st.write("Mapping columns onto the canonical procurement schema")
                run_schema_mapping(run_id)
                status.update(label="Analysis complete", state="complete")
                target = "schema_mapping"
    except Exception as error:
        st.session_state["start_results"] = [("error", f"Analysis failed: {error}")]
        st.rerun()
        return

    st.session_state["upload_round"] = upload_round + 1
    st.session_state["switch_to"] = target
    st.rerun()


def _render_start_results() -> None:
    for level, message in st.session_state.pop("start_results", []):
        getattr(st, level)(message)
