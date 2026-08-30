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
        "canonical spend model and derives procurement value creation levers from it — "
        "a fixed catalogue of fifteen, quantified where the data carries them, with the "
        "gap named where it does not.\n\n"
        "Everything that can be calculated is calculated: amounts, deduplication, "
        "currency conversion and every lever figure come from deterministic code. AI is "
        "used only where meaning has to be interpreted — and every AI proposal is shown "
        "to you for confirmation before it counts."
    )

    st.subheader("The eight steps")
    st.markdown(
        "| # | Step | What happens | Your part | AI |\n"
        "|---|------|--------------|-----------|----|\n"
        "| 1 | Upload *(sidebar)* | Files stored unchanged; encodings, delimiters and "
        "number formats detected | Pick the files, set an AI budget | – |\n"
        "| 2 | Workbook Review | Each sheet classified: transactions, supplier master, "
        "FX rates, documentation | Confirm what each sheet is | proposes |\n"
        "| 3 | Schema Mapping | Columns translated to the canonical schema, confidence "
        "per field | Correct and confirm the mapping | proposes |\n"
        "| 4 | Canonical Table | One portfolio-wide table; quality profiling, flags, EUR "
        "conversion, company and supplier normalization, addressability | Watch it "
        "build | prepares proposals |\n"
        "| 5 | Review & Confirm | The one approval gate for everything that needs "
        "judgement | Edit supplier groups, company merges, exclusions and addressability "
        "— your word is final | you decide |\n"
        "| 6 | Data Quality Report | Completeness, consistency, reconciliation, the "
        "chain from gross to analysable spend | Read | – |\n"
        "| 7 | Procurement Levers | The 15-lever catalogue: quantified where the data "
        "carries it, missing fields named where it does not; every euro counted once | "
        "Read — each saving rate is shown beside its figure | narrative only |\n"
        "| 8 | Executive Summary | Findings, charts, open questions, a chat grounded in "
        "this run's figures; Excel and HTML export | Ask questions, export, share | "
        "summary, questions & chat |"
    )

    proposes, never = st.columns(2)
    with proposes:
        st.subheader("AI proposes, you confirm")
        st.markdown(
            "- which sheet holds data\n"
            "- what each column means\n"
            "- whether two supplier spellings are one firm — only the unclear pairs; "
            "obvious matches merge deterministically\n"
            "- whether a cost type is addressable by procurement\n"
            "- narrative text: recommendations, summary, questions for the business"
        )
    with never:
        st.subheader("Never AI")
        st.markdown(
            "- amounts, totals and every calculation\n"
            "- duplicate detection and currency conversion (ECB reference rates)\n"
            "- company merging\n"
            "- lever quantification and both export files\n"
            "- no row is ever deleted — findings become flags, so every figure "
            "reconciles to the source"
        )

    st.markdown(
        "Every run keeps a complete workspace: each proposal, each confirmation and the "
        "AI cost ledger — the sidebar shows the spend live against your budget."
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


