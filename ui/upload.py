"""Upload screen: stage ERP exports, preview them, then store them into a run."""

import pandas as pd
import streamlit as st

from core.config import ALLOWED_EXTENSIONS, PREVIEW_ROWS
from core.models import ReadOptions
from core.run import create_run, run_path, step_dir_name
from ingestion.readers import list_sheets, read_tabular
from ingestion.storage import STEP, StagedUpload, store_uploads


@st.cache_data(show_spinner=False)
def _parse(data: bytes, filename: str, sheet: str | None) -> tuple[pd.DataFrame, ReadOptions]:
    return read_tabular(data, filename, sheet)


def render() -> None:
    _render_sidebar()

    st.title("Data Upload")
    st.caption(
        "Upload one ERP export per portfolio company. Files are stored unchanged and every "
        "value is read as text, so nothing is reinterpreted before the rule engine runs."
    )

    _render_store_results()

    upload_round = st.session_state.get("upload_round", 0)
    files = st.file_uploader(
        "ERP exports",
        type=list(ALLOWED_EXTENSIONS),
        accept_multiple_files=True,
        key=f"uploader_{upload_round}",
    )

    if files:
        staged = [_stage_file(file) for file in files]
        readable = [item for item in staged if item.get("frame") is not None]
        if st.button("Store datasets", type="primary", disabled=not readable):
            _store(readable, upload_round)


def _render_sidebar() -> None:
    with st.sidebar:
        st.subheader("Current run")
        run_id = st.session_state.get("run_id")
        if run_id is None:
            st.caption("No run started yet. Storing the first upload creates one.")
            return

        st.code(run_id, language=None)
        st.caption(f"Artifacts and logs: {run_path(run_id)}")
        if st.button("New run"):
            del st.session_state["run_id"]
            st.rerun()


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


def _store(staged: list[dict], upload_round: int) -> None:
    run_id = st.session_state.get("run_id") or create_run().run_id
    st.session_state["run_id"] = run_id

    items = [
        StagedUpload(item["data"], item["file"].name, item["company"], item["sheet"])
        for item in staged
    ]
    try:
        manifests = store_uploads(run_id, items)
    except Exception as error:
        st.session_state["store_results"] = [("error", f"Could not store datasets: {error}")]
    else:
        step = step_dir_name(STEP)
        st.session_state["store_results"] = [
            ("success", f"{m.original_filename} -> {run_id}/{step}/{m.stored_filename}")
            for m in manifests
        ]

    st.session_state["upload_round"] = upload_round + 1
    st.rerun()


def _render_store_results() -> None:
    for level, message in st.session_state.pop("store_results", []):
        getattr(st, level)(message)
