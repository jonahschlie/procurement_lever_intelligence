"""Streamlit entrypoint for the Procurement Lever Identification Platform.

Credentials are pushed into the environment here so that everything below the UI
stays free of both dotenv loading and Streamlit imports, and behaves the same
locally as it does on Streamlit Cloud.
"""

import os

import streamlit as st
from dotenv import load_dotenv
from streamlit.errors import StreamlitSecretNotFoundError

from ui import schema_mapping, start

SECRET_KEYS = ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_TIMEOUT")


def _load_credentials() -> None:
    load_dotenv()
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
    "schema_mapping": st.Page(
        schema_mapping.render, title="Schema Mapping", url_path="schema-mapping"
    ),
}

navigation = st.navigation(list(PAGES.values()))

# Screens request a jump by name; the switch has to happen before the page renders.
target = st.session_state.pop("switch_to", None)
if target in PAGES:
    st.switch_page(PAGES[target])

navigation.run()
