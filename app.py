"""Streamlit entrypoint for the Procurement Lever Identification Platform.

Credentials are pushed into the environment here so that everything below the UI
stays free of both dotenv loading and Streamlit imports, and behaves the same
locally as it does on Streamlit Cloud.
"""

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from streamlit.errors import StreamlitSecretNotFoundError

from ui import (
    canonical_table,
    levers,
    report,
    review,
    schema_mapping,
    sidebar,
    start,
    summary,
    workbook_review,
)

SECRET_KEYS = ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_TIMEOUT")


def _load_credentials() -> None:
    # Explicit path: the no-argument form finds the file by inspecting the caller's
    # stack frame, which ties loading to how the process happens to be started.
    load_dotenv(Path(__file__).parent / ".env")
    missing = [key for key in SECRET_KEYS if not os.getenv(key)]
    if not missing:
        return
    try:
        for key in missing:
            if key in st.secrets:
                os.environ[key] = str(st.secrets[key])
    except StreamlitSecretNotFoundError:
        # No secrets.toml. That is the normal case locally, where .env supplies them.
        pass


_load_credentials()

st.set_page_config(page_title="Procurement Lever Intelligence", layout="wide")

PAGES = {
    "start": st.Page(start.render, title="Start", url_path="start", default=True),
    "workbook_review": st.Page(
        workbook_review.render, title="Workbook Review", url_path="workbook-review"
    ),
    "schema_mapping": st.Page(
        schema_mapping.render, title="Schema Mapping", url_path="schema-mapping"
    ),
    "canonical_table": st.Page(
        canonical_table.render, title="Canonical Table", url_path="canonical-table"
    ),
    "review": st.Page(review.render, title="Review & Confirm", url_path="review"),
    "report": st.Page(report.render, title="Data Quality Report", url_path="report"),
    "levers": st.Page(levers.render, title="Procurement Levers", url_path="levers"),
    "summary": st.Page(summary.render, title="Executive Summary", url_path="summary"),
}

navigation = st.navigation(list(PAGES.values()))

# Before the page, so every screen carries the upload or the running AI cost.
sidebar.render()

# Screens request a jump by name; the switch has to happen before the page renders.
target = st.session_state.pop("switch_to", None)
if target in PAGES:
    st.switch_page(PAGES[target])

navigation.run()
