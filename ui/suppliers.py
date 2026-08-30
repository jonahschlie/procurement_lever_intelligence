"""Supplier screen: the merge review queue, then the result."""

import pandas as pd
import streamlit as st

from core.table import load_table
from suppliers.normalization import (
    confirm_suppliers,
    has_artifact,
    has_confirmed,
    load_artifact,
    load_confirmed,
)


def render() -> None:
    st.title("Suppliers")
    st.markdown(
        "Without a supplier identifier, matching works on names: deterministic "
        "similarity merges the unambiguous, the agent judges the unclear, and nothing "
        "becomes canonical without your confirmation. Original names are never "
        "overwritten."
    )

    run_id = st.session_state.get("run_id")
    if run_id is None or not has_artifact(run_id):
        st.info("No supplier matching yet. Run it from the Currency page.")
        return

    if has_confirmed(run_id):
        _render_result(run_id)
        return

    _render_queue(run_id)


def _render_queue(run_id: str) -> None:
    artifact = load_artifact(run_id)
    merges = [group for group in artifact.groups if len(group.members) > 1]
    singles = [group for group in artifact.groups if len(group.members) == 1]

    st.subheader("Proposed merges")
    if not merges:
        st.info("No two names were close enough to propose a merge.")
    else:
        edited = st.data_editor(
            pd.DataFrame(
                [
                    {
                        "Merge": group.approved,
                        "Canonical name": group.canonical_name,
                        "Members": "  |  ".join(group.members),
                        "Rows": group.row_count,
                        "Source": group.source,
                        "Confidence": group.confidence,
                        "Why": group.comment,
                        "Master": group.master_id or "-",
                    }
                    for group in merges
                ]
            ),
            key="merge_queue",
            width="stretch",
            hide_index=True,
            disabled=["Members", "Rows", "Source", "Confidence", "Why", "Master"],
            column_config={
                "Merge": st.column_config.CheckboxColumn(
                    help="Unticked groups fall apart: every member keeps its own identity."
                ),
                "Confidence": st.column_config.NumberColumn(format="%.2f", width="small"),
                "Why": st.column_config.TextColumn(width="large"),
            },
        )
        st.caption(
            f"{len(singles)} name(s) matched nothing and stay as they are. "
            "Unticked ai_unsure groups are the agent's own doubts — look at those first."
        )

    if artifact.rejected:
        with st.expander(f"Pairs the agent kept apart ({len(artifact.rejected)})"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Left": pair.left,
                            "Right": pair.right,
                            "Similarity": pair.similarity,
                            "Why": pair.comment,
                        }
                        for pair in artifact.rejected
                    ]
                ),
                width="stretch",
                hide_index=True,
            )

    if st.button("Confirm suppliers", type="primary"):
        approvals, names = {}, {}
        if merges:
            for group, merge, name in zip(
                merges, edited["Merge"], edited["Canonical name"]
            ):
                approvals[group.group_id] = bool(merge)
                names[group.group_id] = str(name)
        confirm_suppliers(run_id, approvals, names)
        st.rerun()


def _render_result(run_id: str) -> None:
    artifact = load_confirmed(run_id)
    merged = [g for g in artifact.groups if g.approved and len(g.members) > 1]
    canonical = sum(
        1 if group.approved else len(group.members) for group in artifact.groups
    )

    left, middle, right = st.columns(3)
    left.metric("Raw names", artifact.distinct_names)
    middle.metric("Canonical suppliers", canonical)
    right.metric("Merges applied", len(merged))

    st.subheader("Canonical suppliers")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "ID": group.canonical_id,
                    "Canonical name": group.canonical_name,
                    "Members": "  |  ".join(group.members),
                    "Rows": group.row_count,
                    "Country": group.country or "-",
                    "Contract": CONTRACT_TEXT[group.contract_on_file],
                    "Decided by": group.source,
                }
                for group in artifact.groups
                if group.approved
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "The canonical name, id, country and contract status sit in new columns beside "
        "the raw name — `supplier` itself is untouched."
    )

    _render_contract_lever(run_id)


CONTRACT_TEXT = {True: "on file", False: "none", None: "unknown"}


def _render_contract_lever(run_id: str) -> None:
    """Contract coverage by spend -- the simplest form of SYSTEMCONCEPT section 12.

    Spend concentrated on a supplier with no contract on file is a negotiation
    lever that can be read straight off the master, without waiting for the cube.
    """
    table = load_table(run_id)
    if "amount_eur" not in table.columns:
        return

    eligible = table[table["include_supplier_analysis"].astype(bool)].copy()
    eligible = eligible[eligible["amount_eur"].notna()]
    if eligible.empty:
        return

    total = eligible["amount_eur"].sum()
    by_status = eligible.groupby("supplier_contract_status")["amount_eur"].sum()

    st.subheader("Contract coverage")
    st.markdown(
        "Spend sitting with suppliers that have no contract on file is where contract "
        "optimization starts. *Unknown* means the supplier is not in the submitted "
        "master at all — a different statement from having no contract."
    )

    columns = st.columns(3)
    for column, (status, label) in zip(
        columns, [("no", "No contract"), ("yes", "Contract on file"), ("unknown", "Unknown")]
    ):
        value = float(by_status.get(status, 0.0))
        column.metric(
            f"{label} (EUR)", f"{value:,.0f}", delta=f"{value / total:.1%}", delta_color="off"
        )

    uncovered = (
        eligible[eligible["supplier_contract_status"] == "no"]
        .groupby("supplier_normalized")["amount_eur"]
        .agg(["sum", "count"])
        .nlargest(10, "sum")
    )
    if uncovered.empty:
        return

    st.caption("Largest spend without a contract on file")
    st.dataframe(
        pd.DataFrame(
            {
                "Supplier": uncovered.index,
                "Spend (EUR)": uncovered["sum"].round(0),
                "Transactions": uncovered["count"],
                "Share of spend": (uncovered["sum"] / total).map("{:.1%}".format),
            }
        ),
        width="stretch",
        hide_index=True,
    )
