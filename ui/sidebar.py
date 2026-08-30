"""The sidebar, rendered on every screen.

Two blocks that take turns. Before a run exists the sidebar takes the upload and
the budget; from the moment one exists it reports what the agents have cost, and
keeps reporting it on every page — which is the point, since the upload field is
long gone by the time anyone wonders.

The budget is a warning threshold, not a gate. What a call costs is only known
after it was made, so a limit here can report an overrun but not prevent one, and
saying otherwise in the interface would be a lie.
"""

import streamlit as st

from core.config import ALLOWED_EXTENSIONS, DEFAULT_BUDGET_EUR
from core.run import create_run
from ingestion.storage import StagedUpload, store_files
from triage.workbook_triage import run_workbook_triage

BUDGET_HELP = (
    "A warning threshold, not a limit. The price of a call is only known once it "
    "has been made, so this reports an overrun rather than preventing one."
)


def render() -> None:
    with st.sidebar:
        run_id = st.session_state.get("run_id")
        if run_id is None:
            _upload()
        else:
            _usage(run_id)


# --- before a run ----------------------------------------------------------


def _upload() -> None:
    from agents.client import api_key_configured
    from ui.start import check_file

    st.subheader("Upload")
    upload_round = st.session_state.get("upload_round", 0)
    files = st.file_uploader(
        "CSV or Excel export",
        type=list(ALLOWED_EXTENSIONS),
        accept_multiple_files=True,
        key=f"uploader_{upload_round}",
    )
    budget = st.number_input(
        "AI budget (EUR)",
        min_value=0.0,
        value=float(DEFAULT_BUDGET_EUR),
        step=1.0,
        help=BUDGET_HELP,
    )

    staged = [item for item in map(check_file, files or []) if item]
    if st.button("Start analysis", type="primary", disabled=not staged):
        if not api_key_configured():
            st.error(
                "OPENAI_API_KEY is not set, so the agents cannot run. Copy .env.example "
                "to .env and add your key, then restart the app."
            )
            return
        _start(staged, upload_round, budget)

    for level, message in st.session_state.pop("start_results", []):
        getattr(st, level)(message)


def _start(staged: list[dict], upload_round: int, budget: float) -> None:
    # A new run per analysis. Reusing one would overwrite the ingestion artifact
    # while the triage and mapping artifacts of the previous attempt stayed behind,
    # leaving the run describing two different things at once.
    run_id = create_run(budget_eur=budget or None).run_id
    st.session_state["run_id"] = run_id
    items = [StagedUpload(item["data"], item["filename"]) for item in staged]

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


# --- once a run exists -----------------------------------------------------


def _usage(run_id: str) -> None:
    import pandas as pd

    from core import usage

    st.subheader("AI usage")
    spent = usage.total(run_id)
    budget = usage.budget(run_id)

    if budget:
        share = spent.cost_eur / budget
        st.progress(min(share, 1.0))
        if spent.cost_eur > budget:
            st.error(
                f"**{spent.cost_eur:.2f} EUR of {budget:.2f} budgeted** — "
                f"{spent.cost_eur - budget:.2f} over. The analysis is not stopped."
            )
        else:
            st.caption(f"**{spent.cost_eur:.2f} EUR** of {budget:.2f} budgeted · {share:.0%}")
    else:
        st.caption(f"**{spent.cost_eur:.2f} EUR** · no budget set")

    st.caption(f"{spent.calls} call(s) · {spent.tokens:,} tokens")
    if spent.unpriced_calls:
        # Zero would otherwise read as free rather than as unknown.
        st.warning(
            f"{spent.unpriced_calls} call(s) used a model with no price in the table, "
            "so they count as 0 EUR. The token counts are still exact."
        )

    stages = usage.by_stage(run_id)
    if stages:
        with st.expander("Per stage"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Stage": row["stage"],
                            "Calls": row["calls"],
                            "Tokens": row["tokens"],
                            "EUR": round(row["cost_eur"], 4),
                        }
                        for row in stages
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
