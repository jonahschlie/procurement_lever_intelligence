"""Streamlit entrypoint for the Procurement Lever Identification Platform."""

import streamlit as st

from ui import upload

st.set_page_config(
    page_title="Procurement Lever Intelligence",
    layout="wide",
)

st.navigation(
    [st.Page(upload.render, title="Data Upload", url_path="upload", default=True)]
).run()
