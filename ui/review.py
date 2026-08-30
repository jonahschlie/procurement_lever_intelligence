"""The one screen where the user decides.

Everything with a single right answer already happened, silently. What is left
here needs a judgement no measurement can make: which rows are totals, which
suppliers are the group itself, which names are one company, and which cost types
procurement can influence.

Every block arrives preselected. The screen is meant to be read, corrected where
wrong, and confirmed once.
"""

import pandas as pd
import streamlit as st

from classification.spend_classification import (
    confirm_classification,
    has_artifact as has_classification,
)
from classification.spend_classification import load_artifact as load_classification
from fx.currency import load_report as load_currency
from fx.currency import run_currency
from fx.ecb import load_reference_rates
from profiling.data_profiling import confirm_profiling, has_report, load_report
from suppliers.normalization import confirm_suppliers, has_artifact, load_artifact
from transform.rule_engine import run_rule_engine


def render() -> None:
    st.title("Review & Confirm")
    st.markdown(
        "Missing values, duplicates and date problems have been flagged automatically — "
        "they have one correct treatment each and appear in the report. What is left here "
        "needs your judgement. Everything is preselected; change what is wrong and confirm "
        "once."
    )

    run_id = st.session_state.get("run_id")
    if run_id is None or not has_report(run_id) or not has_artifact(run_id):
        st.info("Nothing to review yet. Build the canonical table and run the analysis there.")
        return

    profile = load_report(run_id)
    suppliers = load_artifact(run_id)

    excluded = _aggregates(profile)
    intercompany, approvals, names = _suppliers(suppliers)
    _currency(run_id)
    addressable = _addressability(run_id)

    st.divider()
    if st.button("Confirm and continue", type="primary"):
        _apply(run_id, excluded, intercompany, approvals, names, addressable)


def _aggregates(profile) -> set[int]:
    candidates = profile.aggregate_candidates
    if not candidates:
        return set()

    st.subheader(f"1 · Total rows ({len(candidates)})")
    st.caption(
        "Rows that restate other rows rather than recording a booking. Summing the amount "
        "column without excluding them overstated spend by 2.93x on this data."
    )
    edited = st.data_editor(
        pd.DataFrame(
            [
                {
                    "Exclude": c.exclude,
                    "Source row": c.source_row,
                    "Company": c.company,
                    "Label": c.label,
                    "Amount": c.amount,
                    "Why": "; ".join(c.reasons),
                }
                for c in candidates
            ]
        ),
        key="review_aggregates",
        width="stretch",
        hide_index=True,
        disabled=["Source row", "Company", "Label", "Amount", "Why"],
    )
    return {c.position for c, keep in zip(candidates, edited["Exclude"]) if keep}


def _suppliers(artifact) -> tuple[dict[int, bool], dict[int, bool], dict[int, str]]:
    groups = artifact.groups
    ic_groups = [g for g in groups if g.is_intercompany]
    third_party = [g for g in groups if not g.is_intercompany]

    st.subheader(f"2 · Intercompany ({len(ic_groups)})")
    st.caption(
        "Suppliers that are the group buying from itself. Detected from the company names "
        "in your own data — nothing is hardcoded. Their spend is real but not negotiable, "
        "so they leave the supplier analyses."
    )
    intercompany: dict[int, bool] = {}
    if ic_groups:
        edited = st.data_editor(
            pd.DataFrame(
                [
                    {
                        "Intercompany": True,
                        "Supplier": g.canonical_name,
                        "Names": "  |  ".join(g.members),
                        "Rows": g.row_count,
                        "Why": g.intercompany_reason,
                    }
                    for g in ic_groups
                ]
            ),
            key="review_intercompany",
            width="stretch",
            hide_index=True,
            disabled=["Supplier", "Names", "Rows", "Why"],
        )
        intercompany.update(
            {g.group_id: bool(v) for g, v in zip(ic_groups, edited["Intercompany"])}
        )
    else:
        st.info("No supplier resembles one of the group's own companies.")

    merges = [g for g in third_party if len(g.members) > 1]
    unsure = [g for g in merges if not g.approved]
    settled = [g for g in merges if g.approved]

    st.subheader(f"3 · Supplier consolidation ({len(third_party)} suppliers)")
    st.caption(
        f"{artifact.distinct_names} raw names. Intercompany entities are not shown here."
    )

    approvals: dict[int, bool] = {}
    names: dict[int, str] = {}

    if unsure:
        st.markdown("**Needs a decision** — the agent was not confident:")
        edited = st.data_editor(
            _merge_frame(unsure),
            key="review_unsure",
            width="stretch",
            hide_index=True,
            disabled=["Members", "Rows", "Confidence", "Why"],
        )
        approvals.update({g.group_id: bool(v) for g, v in zip(unsure, edited["Merge"])})
        names.update({g.group_id: str(n) for g, n in zip(unsure, edited["Canonical name"])})

    if settled:
        with st.expander(f"{len(settled)} groups merged automatically — open to review"):
            edited = st.data_editor(
                _merge_frame(settled),
                key="review_settled",
                width="stretch",
                hide_index=True,
                disabled=["Members", "Rows", "Confidence", "Why"],
            )
            approvals.update({g.group_id: bool(v) for g, v in zip(settled, edited["Merge"])})
            names.update({g.group_id: str(n) for g, n in zip(settled, edited["Canonical name"])})

    if artifact.rejected:
        with st.expander(f"{len(artifact.rejected)} pairs the agent kept apart"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Left": p.left, "Right": p.right, "Similarity": p.similarity, "Why": p.comment}
                        for p in artifact.rejected
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
    return intercompany, approvals, names


def _merge_frame(groups) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Merge": g.approved,
                "Canonical name": g.canonical_name,
                "Members": "  |  ".join(g.members),
                "Rows": g.row_count,
                "Confidence": g.confidence,
                "Why": g.comment,
            }
            for g in groups
        ]
    )


def _currency(run_id: str) -> None:
    report = load_currency(run_id)
    st.subheader("4 · Currencies")
    st.caption(
        "Converted at the ECB daily reference rate of each posting date. Nothing to decide "
        "here unless a currency is missing a rate."
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Currency": e.currency,
                    "Rows": e.rows,
                    "Sum (local)": round(e.sum_local, 2),
                    "Rate range": (
                        f"{e.rate_min:,.4f} – {e.rate_max:,.4f}" if e.rate_min is not None else "-"
                    ),
                    "Sum (EUR)": round(e.sum_eur, 2),
                }
                for e in report.breakdown
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    if report.group_unconverted_rows:
        st.warning(
            f"The export's own group amounts equal the local amounts on "
            f"{report.group_unconverted_rows:,} non-EUR rows — they were never converted, so "
            "the EUR figures here come from ECB rates."
        )
    if report.flagged_rows:
        st.info(f"{report.flagged_rows:,} rows have an amount but no usable rate. Flagged, not guessed.")


def _addressability(run_id: str) -> dict[str, bool]:
    if not has_classification(run_id):
        return {}
    artifact = load_classification(run_id)
    if not artifact.cost_types:
        return {}

    st.subheader(f"5 · Addressable spend ({len(artifact.cost_types)} cost types)")
    st.caption(
        "Payroll, taxes, interest and provisions sit in the same ledger as consulting and "
        "freight, but procurement cannot negotiate them. Untick what it cannot influence."
    )
    edited = st.data_editor(
        pd.DataFrame(
            [
                {
                    "Addressable": c.addressable,
                    "Cost type": c.cost_type,
                    "Spend (EUR)": round(c.spend, 0),
                    "Rows": c.rows,
                    "Confidence": c.confidence,
                    "Why": c.comment,
                }
                for c in artifact.cost_types
            ]
        ),
        key="review_addressability",
        width="stretch",
        hide_index=True,
        disabled=["Cost type", "Spend (EUR)", "Rows", "Confidence", "Why"],
    )
    return {c.cost_type: bool(v) for c, v in zip(artifact.cost_types, edited["Addressable"])}


def _apply(run_id, excluded, intercompany, approvals, names, addressable) -> None:
    """Apply every decision, then recompute what depends on it. No model calls here."""
    with st.status("Applying your decisions", expanded=True) as status:
        st.write("Recording total rows and category usability")
        confirm_profiling(run_id, excluded=excluded)

        st.write("Re-flagging rows")
        run_rule_engine(run_id)

        st.write("Writing canonical suppliers and intercompany")
        confirm_suppliers(run_id, approvals, names, intercompany)

        if addressable:
            st.write("Writing addressability")
            confirm_classification(run_id, addressable)

        # Eligibility changed, so flags derived from it are refreshed once more.
        st.write("Recomputing spend")
        run_rule_engine(run_id)
        run_currency(run_id, load_reference_rates())
        status.update(label="Confirmed", state="complete")

    st.session_state["switch_to"] = "report"
    st.rerun()
