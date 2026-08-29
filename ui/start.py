"""Start screen: what the platform does, then upload and launch the analysis."""

import pandas as pd
import streamlit as st

from agents.client import api_key_configured
from core.config import ALLOWED_EXTENSIONS, PREVIEW_ROWS
from core.models import ReadOptions
from core.run import create_run
from ingestion.readers import list_sheets, read_tabular
from ingestion.storage import StagedUpload, store_uploads
from mapping.schema_mapping import run_schema_mapping
from ui.sidebar import render_run_sidebar


@st.cache_data(show_spinner=False)
def _parse(data: bytes, filename: str, sheet: str | None) -> tuple[pd.DataFrame, ReadOptions]:
    return read_tabular(data, filename, sheet)


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
        "meaning has to be interpreted, beginning with the step below, which translates "
        "your export's column names into the canonical procurement schema."
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
        sheet = _sheet_selector(data, file)
        company = st.text_input(
            "Portfolio company (optional)",
            key=f"company_{file.file_id}",
            help="Fallback only. A company column inside the export takes precedence.",
        )
        try:
            frame, options = _parse(data, file.name, sheet)
        except Exception as error:
            # Batch upload: one unreadable file must not block the others, so the
            # failure is surfaced here instead of propagating.
            st.error(f"Could not read this file: {error}")
            return {"file": file}

        st.caption(_describe(frame, options))
        st.dataframe(frame.head(PREVIEW_ROWS), width="stretch")
        return {
            "file": file,
            "data": data,
            "company": company,
            "sheet": sheet,
            "frame": frame,
        }


def _sheet_selector(data: bytes, file) -> str | None:
    if not file.name.lower().endswith(".xlsx"):
        return None
    sheets = list_sheets(data)
    if len(sheets) < 2:
        return None
    return st.selectbox("Sheet", sheets, key=f"sheet_{file.file_id}")


def _describe(frame: pd.DataFrame, options: ReadOptions) -> str:
    parts = [f"{len(frame):,} rows x {len(frame.columns)} columns"]
    if options.delimiter:
        parts.append(f"delimiter {options.delimiter!r}")
    if options.encoding:
        parts.append(f"encoding {options.encoding}")
    if options.sheet:
        parts.append(f"sheet {options.sheet}")
    return "  |  ".join(parts)


def _start_analysis(staged: list[dict], upload_round: int) -> None:
    if not api_key_configured():
        st.error(
            "OPENAI_API_KEY is not set, so the schema mapping agent cannot run. "
            "Copy .env.example to .env and add your key, then restart the app."
        )
        return

    run_id = st.session_state.get("run_id") or create_run().run_id
    st.session_state["run_id"] = run_id
    items = [
        StagedUpload(item["data"], item["file"].name, item["company"], item["sheet"])
        for item in staged
    ]

    try:
        with st.status("Running analysis", expanded=True) as status:
            st.write(f"Storing {len(items)} dataset(s) in {run_id}")
            store_uploads(run_id, items)
            st.write("Mapping columns onto the canonical procurement schema")
            run_schema_mapping(run_id)
            status.update(label="Analysis complete", state="complete")
    except Exception as error:
        st.session_state["start_results"] = [("error", f"Analysis failed: {error}")]
        st.rerun()
        return

    st.session_state["upload_round"] = upload_round + 1
    st.session_state["switch_to"] = "schema_mapping"
    st.rerun()


def _render_start_results() -> None:
    for level, message in st.session_state.pop("start_results", []):
        getattr(st, level)(message)
