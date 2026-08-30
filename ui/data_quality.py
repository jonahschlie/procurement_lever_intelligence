"""Data quality screen: what profiling found, and what the rules did about it."""

import pandas as pd
import streamlit as st

from core.config import PREVIEW_ROWS
from core.models import ProfilingReport
from core.table import load_table
from profiling.data_profiling import confirm_profiling, has_report, load_report
from transform.rule_engine import has_report as has_rule_report
from transform.rule_engine import load_report as load_rule_report
from transform.rule_engine import run_rule_engine

SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡", "info": "⚪"}


def render() -> None:
    st.title("Data Quality")
    st.markdown(
        "Profiling measures, the rule engine acts. No row is ever removed — every "
        "finding becomes a flag, and each analysis decides for itself which rows it "
        "may use."
    )

    run_id = st.session_state.get("run_id")
    if run_id is None or not has_report(run_id):
        st.info("No quality report yet. Build the canonical table and run the checks there.")
        return

    if has_rule_report(run_id):
        _render_result(run_id)
        return

    _render_findings(run_id, load_report(run_id))


def _render_findings(run_id: str, report: ProfilingReport) -> None:
    st.subheader("Findings")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "": SEVERITY_ICON[finding.severity],
                    "Check": finding.check,
                    "Result": finding.result,
                    "Rows": finding.affected_rows,
                    "What it means": finding.detail,
                }
                for finding in report.findings
            ]
        ),
        width="stretch",
        hide_index=True,
        column_config={"": st.column_config.TextColumn(width="small")},
    )

    excluded = _render_aggregates(report)
    category_enabled = _render_category(report)
    _render_reconciliation(report)

    st.divider()
    if st.button("Apply rules", type="primary"):
        _apply(run_id, excluded, category_enabled)


def _render_aggregates(report: ProfilingReport) -> set[int]:
    if not report.aggregate_candidates:
        return set()

    st.subheader("Aggregate rows")
    st.markdown(
        "These rows look like totals the export computed for itself rather than "
        "bookings. Detection is a heuristic, and the effect on spend is large, so "
        "nothing is excluded without your say-so."
    )

    edited = st.data_editor(
        pd.DataFrame(
            [
                {
                    "Exclude": candidate.exclude,
                    "Source row": candidate.source_row,
                    "Company": candidate.company,
                    "Label": candidate.label,
                    "Amount": candidate.amount,
                    "Why": "; ".join(candidate.reasons),
                }
                for candidate in report.aggregate_candidates
            ]
        ),
        key="aggregate_candidates",
        width="stretch",
        hide_index=True,
        disabled=["Source row", "Company", "Label", "Amount", "Why"],
    )
    return {
        candidate.position
        for candidate, keep in zip(report.aggregate_candidates, edited["Exclude"])
        if keep
    }


def _render_category(report: ProfilingReport) -> bool:
    st.subheader("Category analysis")
    st.caption(report.category_decision)
    return st.checkbox(
        "Use the procurement category for analysis",
        value=report.category_analysis_enabled,
        help="Switched off automatically when the category only repeats the GL description.",
    )


def _render_reconciliation(report: ProfilingReport) -> None:
    if not report.reconciliation:
        return

    gap = sum(entry.difference for entry in report.reconciliation)
    if abs(gap) > 0.01:
        st.warning(
            f"Detail exceeds the subtotals this export states for itself by {gap:,.2f}. "
            "The analysis proceeds, but the difference is unexplained and stays in the report."
        )

    with st.expander("Reconciliation per company"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Company": entry.company,
                        "Detail rows": entry.detail_rows,
                        "Detail total": round(entry.detail_total, 2),
                        "Stated subtotal": round(entry.stated_total, 2),
                        "Difference": round(entry.difference, 2),
                    }
                    for entry in report.reconciliation
                ]
            ),
            width="stretch",
            hide_index=True,
        )


def _apply(run_id: str, excluded: set[int], category_enabled: bool) -> None:
    with st.status("Applying rules", expanded=True) as status:
        st.write("Recording your decisions")
        confirm_profiling(run_id, excluded=excluded, category_enabled=category_enabled)
        st.write("Flagging rows and deciding eligibility")
        report = run_rule_engine(run_id)
        status.update(
            label=f"Spend after exclusions: {report.spend_after:,.2f}", state="complete"
        )
    st.rerun()


def _render_result(run_id: str) -> None:
    report = load_rule_report(run_id)

    st.subheader("Result")
    left, middle, right = st.columns(3)
    left.metric("Spend before", f"{report.spend_before:,.0f}")
    middle.metric(
        "Spend after exclusions",
        f"{report.spend_after:,.0f}",
        delta=f"-{report.spend_before - report.spend_after:,.0f}",
        delta_color="off",
    )
    right.metric("Rows excluded", f"{report.excluded_rows:,} of {report.row_count:,}")

    st.caption(
        "The row count is unchanged — exclusions are flags, so the table still "
        "reconciles against the source export."
    )

    st.subheader("Eligibility")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Analysis": column.replace("include_", "").replace("_", " ").title(),
                    "Eligible rows": count,
                    "Share": f"{count / report.row_count:.1%}",
                }
                for column, count in report.eligibility.items()
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Rules that fired")
    st.dataframe(
        pd.DataFrame(
            [
                {"Rule": effect.rule, "Rows": effect.affected_rows, "Effect": effect.detail}
                for effect in report.effects
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Preview")
    table = load_table(run_id)
    columns = [c for c in table.columns if c.startswith(("include_", "flag_"))]
    st.dataframe(
        table[["source_row", "supplier", "amount_group_value", *columns]].head(PREVIEW_ROWS),
        width="stretch",
        hide_index=True,
    )
