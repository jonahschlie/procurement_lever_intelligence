"""Lever screen: where the savings are, how much, and why in this order.

Every figure is shown next to the rate that produced it. The saving rates are
assumptions and are labelled as such; the bases and the assignment come from the
data. Each lever can be opened down to the individual bookings behind it.
"""

import pandas as pd
import streamlit as st

from core.config import LEVER_PRECEDENCE
from core.table import load_table
from levers.definitions import BY_ID
from levers.engine import has_artifact, load_artifact

LEVEL_ICON = {"low": "🟢", "medium": "🟠", "high": "🔴"}
CONFIDENCE_ICON = {"high": "🟢", "medium": "🟠", "low": "🔴"}


def render() -> None:
    st.title("Procurement Levers")

    run_id = st.session_state.get("run_id")
    if run_id is None or not has_artifact(run_id):
        st.info("No levers yet. Open the data quality report and identify them there.")
        return

    artifact = load_artifact(run_id)
    table = load_table(run_id)

    quantified = [l for l in artifact.levers if l.status == "quantified" and l.kind != "risk"]
    risks = [l for l in artifact.levers if l.status == "quantified" and l.kind == "risk"]
    absent = [l for l in artifact.levers if l.status == "not_applicable"]
    blocked = [l for l in artifact.levers if l.status == "not_assessable"]

    _headline(artifact, len(quantified), len(artifact.levers))
    _priority(artifact, quantified)
    for rank, lever in enumerate(quantified, start=1):
        _lever(rank, lever, table)
    _risks(risks)
    _absent(absent)
    _blocked(blocked, artifact)
    _benchmark(artifact)
    _assumptions(artifact)

    st.divider()
    if st.button("Build executive summary", type="primary"):
        _build_summary(run_id)


def _build_summary(run_id: str) -> None:
    from analysis.summary import build_summary, has_summary

    with st.status("Building the executive summary", expanded=True) as status:
        if not has_summary(run_id):
            st.write("Gathering the findings and drafting questions for the business")
            summary = build_summary(run_id)
            status.update(
                label=f"{len(summary.sections)} sections, {len(summary.sme_questions)} questions",
                state="complete",
            )
        else:
            status.update(label="Ready", state="complete")
    st.session_state["switch_to"] = "summary"
    st.rerun()


def _headline(artifact, found: int, total: int) -> None:
    left, middle, right = st.columns(3)
    left.metric("Potential — low", f"{artifact.total_low:,.0f}")
    middle.metric("Potential — base", f"{artifact.total_base:,.0f}")
    right.metric("Potential — high", f"{artifact.total_high:,.0f}")
    base = artifact.total_base / artifact.addressable_spend if artifact.addressable_spend else 0
    st.caption(
        f"EUR, against {artifact.addressable_spend:,.0f} of addressable spend "
        f"({base:.1%} in the base case). Every euro counts towards one lever only. "
        f"{found} of {total} levers in the standard catalogue are quantified here; "
        "the rest are listed below with the reason."
    )
    st.warning(
        "**The saving percentages are assumptions, not findings.** They are practitioner "
        "ranges, shown next to every figure they produce and replaceable in the "
        "configuration. What comes from your data is the spend each lever applies to, "
        "which bookings those are, and how they were assigned."
    )


def _priority(artifact, quantified) -> None:
    st.subheader("Priority")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "#": rank,
                    "Lever": lever.name,
                    "Spend it applies to": round(lever.net_base, 0),
                    "Potential (base)": round(lever.potential_base, 0),
                    "Range": f"{lever.potential_low:,.0f} – {lever.potential_high:,.0f}",
                    "Effort": f"{LEVEL_ICON[lever.effort]} {lever.effort}",
                    "Confidence": f"{CONFIDENCE_ICON[lever.confidence]} {lever.confidence}",
                }
                for rank, lever in enumerate(quantified, start=1)
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    st.caption("Ranked by potential in the base case; ties go to the lever needing less coordination.")

    if artifact.priority_rationale:
        st.markdown(artifact.priority_rationale)

    ranked = [lever.lever_id for lever in quantified]
    if artifact.agent_order and artifact.agent_order != ranked:
        with st.expander("The agent would tackle them in a different order"):
            st.markdown(
                "**Suggested:** "
                + " → ".join(BY_ID[i].name for i in artifact.agent_order if i in BY_ID)
            )
            st.markdown(artifact.agent_order_reason)


def _lever(rank: int, lever, table: pd.DataFrame) -> None:
    with st.expander(
        f"{rank} · {lever.name} — {lever.potential_base:,.0f} EUR (base)", expanded=rank == 1
    ):
        st.markdown(f"*{lever.mechanism}*")
        if lever.opportunity:
            st.markdown(lever.opportunity)
        if lever.next_steps:
            st.markdown("**Next steps**")
            for step in lever.next_steps:
                st.markdown(f"- {step}")

        st.markdown("**How the figure is built**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Scenario": name,
                        "Spend it applies to": round(lever.net_base, 0),
                        "Rate": f"{rate:.0%}",
                        "Potential (EUR)": round(lever.net_base * rate, 0),
                    }
                    for name, rate in (
                        ("Low", lever.rate_low),
                        ("Base", lever.rate_base),
                        ("High", lever.rate_high),
                    )
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        claimed = lever.gross_base - lever.net_base
        if claimed > 0:
            st.caption(
                f"On its own the lever covers {lever.gross_base:,.0f} EUR. "
                f"{claimed:,.0f} of that is counted under a more specific lever, so only "
                f"{lever.net_base:,.0f} is credited here."
            )

        st.markdown(
            f"**Confidence:** {CONFIDENCE_ICON[lever.confidence]} {lever.confidence} — "
            f"{lever.confidence_reason}  \n"
            f"**Effort:** {LEVEL_ICON[lever.effort]} {lever.effort} — {lever.effort_reason}"
        )

        if lever.contributors:
            st.markdown("**Largest contributors**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Supplier": c.supplier,
                            "Spend (EUR)": round(c.spend, 0),
                            "Companies": c.companies,
                            "Bookings": c.rows,
                            "Contract": c.contract_status or "-",
                        }
                        for c in lever.contributors
                    ]
                ),
                width="stretch",
                hide_index=True,
            )

        _bookings(lever, table)


def _bookings(lever, table: pd.DataFrame) -> None:
    """The rows behind the figure, traceable back to the source file."""
    column = f"lever_{lever.lever_id}"
    if column not in table.columns:
        return
    rows = table[table[column].fillna(False).astype(bool)]
    if rows.empty:
        st.caption("No bookings qualify for this lever.")
        return

    assigned = rows[rows["lever_primary"] == lever.lever_id]
    st.markdown("**Which bookings**")
    st.caption(
        f"{len(rows):,} bookings across {lever.suppliers} supplier(s) and "
        f"{lever.companies} company(ies); {len(assigned):,} of them are credited to this "
        "lever. `source_row` points back into the uploaded file."
    )
    st.dataframe(
        rows[
            [
                "source_row",
                "company_name",
                "supplier_normalized",
                "amount_eur",
                "posting_date",
                "supplier_contract_status",
                "lever_primary",
            ]
        ]
        .sort_values("amount_eur", ascending=False)
        .head(200),
        width="stretch",
        hide_index=True,
    )
    if len(rows) > 200:
        st.caption(f"Showing the 200 largest of {len(rows):,} bookings.")


def _risks(levers) -> None:
    if not levers:
        return
    st.subheader("Risk exposures")
    st.caption(
        "Measured like the levers above, but deliberately **not** added to the "
        "potential: an exposure is something to manage, not a saving to book."
    )
    for lever in levers:
        st.markdown(f"**{lever.name}** — {lever.metric}")
        st.caption(lever.mechanism)


def _absent(levers) -> None:
    if not levers:
        return
    st.subheader("Checked, nothing found")
    st.caption("These levers were measurable against the data and turned up nothing.")
    st.dataframe(
        pd.DataFrame(
            [{"Lever": l.name, "Why": l.status_reason} for l in levers]
        ),
        width="stretch",
        hide_index=True,
    )


def _blocked(levers, artifact) -> None:
    if not levers:
        return
    st.subheader("Not assessable from this data")
    st.caption(
        "Part of the standard catalogue, but this submission does not carry what "
        "they are measured from. Listed so a zero is never mistaken for an absence "
        "of opportunity."
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Lever": l.name,
                    "What it would find": l.mechanism,
                    "Why not": l.status_reason,
                }
                for l in levers
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    if artifact.data_requests:
        st.markdown("**What to ask the portfolio company for**")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Field": r.label, "Would unlock": ", ".join(r.unlocks)}
                    for r in artifact.data_requests
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "Each of these is a canonical field the mapping already looks for. "
            "Supplying it in the next data request turns the lever above into a "
            "measured figure."
        )


def _benchmark(artifact) -> None:
    if not artifact.benchmark:
        return
    st.subheader("Where to start: the companies compared")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Company": e.company,
                    "Spend (EUR)": round(e.spend, 0),
                    "Suppliers": e.suppliers,
                    "PO coverage": f"{e.po_coverage:.1%}",
                    "Without contract": f"{e.uncontracted_share:.1%}",
                }
                for e in artifact.benchmark
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "Sorted by the share of spend without a contract. The spread between companies is "
        "itself the opportunity: whatever the best one does, the others can copy."
    )


def _assumptions(artifact) -> None:
    with st.expander("Assumptions and method"):
        st.markdown("**Saving rates applied** — practitioner ranges, not derived from this data:")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Lever": lever.name,
                        "Low": f"{lever.rate_low:.0%}",
                        "Base": f"{lever.rate_base:.0%}",
                        "High": f"{lever.rate_high:.0%}",
                    }
                    for lever in artifact.levers
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        st.markdown(
            "**No euro is counted twice.** A booking can qualify for several levers, so "
            "each one is credited to exactly one — the most specific population first:\n\n"
            + " → ".join(BY_ID[i].name for i in LEVER_PRECEDENCE if i in BY_ID)
            + "\n\nSpecificity is a property of the data. Ordering by assumed saving rate "
            "instead would maximise the total and bias it optimistic.\n\n"
            "**Confidence** says how far a base rests on evidence rather than on absence "
            "of data. **Effort** counts the suppliers and companies to be coordinated. "
            "Both are computed, not judged.\n\n"
            "The agent writes the narrative and may suggest a different order. It is given "
            "aggregates only, never individual bookings, and its output model has no "
            "numeric field — every figure on this page is arithmetic."
        )
