"""The data quality report: what happened to the data, in business terms.

Nothing here asks anything. It answers the questions a reader has after the fact:
how much was missing, how it was treated, what the currency conversion changed,
how much of the spend is the group buying from itself, and how much is left that
procurement can actually negotiate.
"""

import pandas as pd
import streamlit as st

from analysis.spend_report import build_spend_report
from core.table import has_table, load_table
from fx.currency import has_report as has_currency
from fx.currency import load_report as load_currency
from profiling.data_profiling import load_report as load_profile
from suppliers.normalization import has_confirmed, load_confirmed
from ui.format import as_money, eur, eur_compact, money

SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡", "info": "⚪"}


def render() -> None:
    st.title("Data Quality Report")

    run_id = st.session_state.get("run_id")
    if run_id is None or not has_table(run_id) or not has_confirmed(run_id):
        st.info("No report yet. Confirm your decisions on the review screen first.")
        return

    table = load_table(run_id)
    report = build_spend_report(run_id)

    _chain(report)
    _currency(run_id)
    _completeness(run_id, table)
    _suppliers(run_id, report, table)

    st.divider()
    if st.button("Identify levers", type="primary"):
        _identify_levers(run_id)


def _identify_levers(run_id: str) -> None:
    from levers.engine import has_artifact, run_levers

    with st.status("Identifying levers", expanded=True) as status:
        if not has_artifact(run_id):
            st.write("Measuring each lever and assigning every euro to one of them")
            artifact = run_levers(run_id)
        else:
            from levers.engine import load_artifact

            artifact = load_artifact(run_id)
        status.update(
            label=f"{len(artifact.levers)} levers, {eur(artifact.total_base)} EUR (base)",
            state="complete",
        )
    st.session_state["switch_to"] = "levers"
    st.rerun()


def _chain(report) -> None:
    st.subheader("From booked to negotiable")
    steps = {step.label: step for step in report.chain}

    left, middle, right = st.columns(3)
    for column, label, step in (
        (left, "Net spend", "Net spend"),
        (middle, "Third party", "Third party spend"),
        (right, "Addressable", "Addressable spend"),
    ):
        amount = steps[step].amount
        column.metric(f"{label} (EUR)", eur_compact(amount), help=eur(amount))

    total = steps["Net spend"].amount or 1
    st.dataframe(
        as_money(
            pd.DataFrame(
                [
                    {
                        "": "−" if step.delta else "=",
                        "Step": step.label,
                        "EUR": step.amount,
                        "Share of net": f"{step.amount / total:.1%}",
                        "Note": step.note,
                    }
                    for step in report.chain
                ]
            ),
            "EUR",
        ),
        width="stretch",
        hide_index=True,
        column_config={"": st.column_config.TextColumn(width="small"), "EUR": money()},
    )
    st.caption(
        f"{report.rows_analysed:,} of {report.rows_total:,} rows enter the analysis. "
        "The rest are total rows or rows without a usable amount — flagged, never deleted, "
        "so the table still reconciles against the source export."
    )


def _currency(run_id: str) -> None:
    if not has_currency(run_id):
        return
    report = load_currency(run_id)

    st.subheader("What the currency conversion changed")
    st.dataframe(
        as_money(
            pd.DataFrame(
                [
                    {
                        "Currency": e.currency,
                        "Rows": e.rows,
                        "Sum (local)": e.sum_local,
                        "Rate range": (
                            f"{e.rate_min:,.4f} – {e.rate_max:,.4f}"
                            if e.rate_min is not None
                            else "-"
                        ),
                        "Sum (EUR)": e.sum_eur,
                    }
                    for e in report.breakdown
                ]
            ),
            "Sum (local)",
            "Sum (EUR)",
        ),
        width="stretch",
        hide_index=True,
        column_config={"Sum (local)": money(), "Sum (EUR)": money()},
    )
    st.caption(
        f"Converted at {report.rate_source} daily rates ({report.rates_frozen_to}, frozen into "
        f"the run). {report.group_unconverted_rows:,} rows had a group amount that was never "
        "converted in the source, so it was used as a cross-check only."
    )


def _completeness(run_id: str, table: pd.DataFrame) -> None:
    profile = load_profile(run_id)

    st.subheader("What was missing, and how it was handled")
    handling = {
        "completeness": "Flagged; excluded from the analyses that need the field",
        "consistency": "Flagged; no row removed and no value corrected",
        "semantic": "Flagged; affects which analyses are available",
        "aggregates": "Excluded from spend after your confirmation",
        "reconciliation": "Reported; the difference is not corrected",
        "readiness": "Informational",
    }
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "": SEVERITY_ICON[f.severity],
                    "Check": f.check,
                    "Result": f.result,
                    "Rows": f.affected_rows,
                    "Handling": handling.get(f.category, ""),
                }
                for f in profile.findings
            ]
        ),
        width="stretch",
        hide_index=True,
        column_config={"": st.column_config.TextColumn(width="small")},
    )
    st.caption(profile.category_decision)


def _suppliers(run_id: str, report, table: pd.DataFrame) -> None:
    artifact = load_confirmed(run_id)
    canonical = [g for g in artifact.groups if g.approved and not g.is_intercompany]

    st.subheader("Suppliers")
    left, middle, right = st.columns(3)
    left.metric("Raw names", artifact.distinct_names)
    middle.metric("Third party suppliers", len(canonical))
    right.metric(
        "Intercompany",
        f"{report.intercompany_rows:,} rows",
        delta=f"{len(report.intercompany_suppliers)} entities",
        delta_color="off",
    )

    if report.intercompany_suppliers:
        st.caption("Group entities billing the group: " + ", ".join(report.intercompany_suppliers))

    if "supplier_contract_status" not in table.columns:
        return

    eligible = table[table["include_supplier_analysis"].astype(bool)]
    eligible = eligible[eligible["amount_eur"].notna()]
    if eligible.empty:
        return

    total = eligible["amount_eur"].sum()
    by_status = eligible.groupby("supplier_contract_status")["amount_eur"].sum()
    st.caption("Contract coverage of third party spend")
    st.dataframe(
        as_money(
            pd.DataFrame(
                [
                    {
                        "Contract": label,
                        "Spend (EUR)": float(by_status.get(key, 0.0)),
                        "Share": f"{float(by_status.get(key, 0.0)) / total:.1%}",
                    }
                    for key, label in (
                        ("no", "None on file"),
                        ("yes", "On file"),
                        ("unknown", "Not in the master"),
                    )
                ]
            ),
            "Spend (EUR)",
        ),
        width="stretch",
        hide_index=True,
        column_config={"Spend (EUR)": money()},
    )
