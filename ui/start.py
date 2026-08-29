"""Start screen: explain the platform, take the uploads, launch the analysis.

The main area stays an explanation. Everything the user operates lives in the
sidebar, and the first look at the data is the workbook review -- which shows
what was actually recognised rather than a preview of bytes that may well be a
cover letter.
"""

import streamlit as st

from agents.client import api_key_configured
from core.config import ALLOWED_EXTENSIONS
from core.run import create_run
from ingestion.readers import file_format, file_options, list_sheets
from ingestion.storage import StagedUpload, store_files
from triage.workbook_triage import run_workbook_triage
from ui.sidebar import render_run_sidebar


@st.cache_data(show_spinner=False)
def _unreadable_reason(data: bytes, filename: str) -> str | None:
    """Cheap check that the file can be opened at all.

    Deliberately not a full parse: this only has to catch a file that cannot be
    read, early enough to name which one, without spending time on the others.
    """
    try:
        fmt = file_format(filename)
        file_options(data, fmt)
        if fmt == "xlsx":
            list_sheets(data)
    except Exception as error:
        return str(error)
    return None


def render() -> None:
    staged = _render_sidebar()

    st.title("Procurement Lever Intelligence")
    st.markdown(
        "Welcome. This platform turns the ERP exports of portfolio companies into one "
        "standardized procurement data model, and uses it to surface value creation "
        "levers across the portfolio: supplier consolidation, category bundling, tail "
        "spend reduction and contract optimization.\n\n"
        "Anything that can be calculated is calculated. Spend figures, aggregations and "
        "quality checks never pass through a language model. AI is used only where "
        "meaning has to be interpreted."
    )

    st.subheader("How it works")
    st.markdown(
        "1. **Upload** one ERP export per portfolio company in the sidebar. Files are "
        "stored unchanged and every value is read as text, so nothing is reinterpreted.\n"
        "2. **Workbook review** — a submission is usually a workbook rather than a table. "
        "Shape decides which sheets are data at all; an agent decides what each table is "
        "for. You confirm before anything is analysed.\n"
        "3. **Schema mapping** — the transaction columns are translated into the canonical "
        "procurement schema, with a confidence score and a comment per field, which you "
        "can correct."
    )

    if not staged:
        st.info("Upload an ERP export in the sidebar to begin.")


def _render_sidebar() -> list[dict]:
    with st.sidebar:
        st.subheader("Upload")
        upload_round = st.session_state.get("upload_round", 0)
        files = st.file_uploader(
            "CSV or Excel export",
            type=list(ALLOWED_EXTENSIONS),
            accept_multiple_files=True,
            key=f"uploader_{upload_round}",
        )

        staged = [item for item in map(_stage_file, files or []) if item]
        if st.button("Start analysis", type="primary", disabled=not staged):
            _start_analysis(staged, upload_round)

        _render_start_results()
        st.divider()

    render_run_sidebar()
    return staged


def _stage_file(file) -> dict | None:
    data = file.getvalue()
    reason = _unreadable_reason(data, file.name)
    if reason:
        # One unreadable file must not block the others.
        st.error(f"{file.name}: {reason}")
        return None

    company = st.text_input(
        "Portfolio company",
        key=f"company_{file.file_id}",
        placeholder=file.name,
        help="Used as the display name for this export throughout the analysis.",
    )
    return {"data": data, "filename": file.name, "company": company}


def _start_analysis(staged: list[dict], upload_round: int) -> None:
    if not api_key_configured():
        st.error(
            "OPENAI_API_KEY is not set, so the agents cannot run. Copy .env.example to "
            ".env and add your key, then restart the app."
        )
        return

    run_id = st.session_state.get("run_id") or create_run().run_id
    st.session_state["run_id"] = run_id
    items = [StagedUpload(item["data"], item["filename"], item["company"]) for item in staged]

    try:
        with st.status("Reading uploads", expanded=True) as status:
            st.write(f"Storing {len(items)} file(s)")
            store_files(run_id, items)
            st.write("Working out which sheets hold data")
            run_workbook_triage(run_id)
            status.update(label="Sheets identified", state="complete")
    except Exception as error:
        st.session_state["start_results"] = [("error", f"Could not read the uploads: {error}")]
        st.rerun()
        return

    st.session_state["upload_round"] = upload_round + 1
    st.session_state["switch_to"] = "workbook_review"
    st.rerun()


def _render_start_results() -> None:
    for level, message in st.session_state.pop("start_results", []):
        getattr(st, level)(message)
