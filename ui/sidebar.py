"""Run context, shown on every screen."""

import streamlit as st

from core.run import run_path


def render_run_sidebar() -> None:
    with st.sidebar:
        st.subheader("Current run")
        run_id = st.session_state.get("run_id")
        if run_id is None:
            st.caption("No run started yet. Starting an analysis creates one.")
            return

        st.code(run_id, language=None)
        st.caption(f"Artifacts and logs: {run_path(run_id)}")
        if st.button("New run"):
            del st.session_state["run_id"]
            st.rerun()
