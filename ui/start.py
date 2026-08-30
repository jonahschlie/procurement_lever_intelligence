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
        "can correct.\n"
        "4. **Data quality** — the table is measured for completeness, consistency and "
        "embedded totals. Findings become flags; no row is ever deleted, so the figures "
        "stay reconcilable against the source."
    )

    st.info("Upload an ERP export in the sidebar to begin.")


def check_file(file) -> dict | None:
    data = file.getvalue()
    reason = _unreadable_reason(data, file.name)
    if reason:
        # One unreadable file must not block the others.
        st.error(f"{file.name}: {reason}")
        return None
    return {"data": data, "filename": file.name}


