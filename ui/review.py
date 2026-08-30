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

from companies.normalization import confirm_companies
from companies.normalization import has_artifact as has_companies
from companies.normalization import load_artifact as load_companies
from classification.spend_classification import (
    confirm_classification,
    has_artifact as has_classification,
)
from classification.spend_classification import load_artifact as load_classification
from fx.currency import load_report as load_currency
from fx.currency import run_currency
from fx.ecb import load_reference_rates
from profiling.data_profiling import confirm_profiling, has_report, load_report
from suppliers.normalization import (
    confirm_suppliers,
    has_artifact,
    load_artifact,
    name_volumes,
)
from transform.rule_engine import run_rule_engine
from core.config import CONFIDENCE_THRESHOLD
from ui.format import as_money, eur, money, rate as rate_column


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
    company_approvals, company_names = _companies(run_id)
    intercompany, assignments = _suppliers(run_id, suppliers)
    _currency(run_id)
    addressable = _addressability(run_id)

    st.divider()
    if st.button("Confirm and continue", type="primary"):
        _apply(
            run_id,
            excluded,
            company_approvals,
            company_names,
            intercompany,
            assignments,
            addressable,
        )


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


def _companies(run_id: str) -> tuple[dict[int, bool], dict[int, str]]:
    """Which spellings are the same legal entity.

    One workbook spells its companies consistently, so this block is usually a
    glance. It earns its place with a submission per portfolio company, where the
    same entity arrives spelled differently -- and where two ERPs numbering their
    entities from 1000 would otherwise merge two unrelated companies.
    """
    if not has_companies(run_id):
        return {}, {}
    artifact = load_companies(run_id)
    if not artifact.groups:
        return {}, {}

    st.subheader(f"2 · Companies ({len(artifact.groups)})")
    st.caption(
        f"{artifact.distinct_names} company spellings across the submission. Everything the "
        "portfolio benchmark, the contract coverage and supplier consolidation count per "
        "company follows from these groups."
    )

    collisions = [g for g in artifact.groups if g.code_collision]
    if collisions:
        st.warning(
            f"{len(collisions)} company code(s) appear in more than one export with an "
            "unrelated name. They are kept apart — tick Same company only if they really are one."
        )

    edited = st.data_editor(
        pd.DataFrame(
            [
                {
                    "Same company": g.approved,
                    "Canonical name": g.canonical_name,
                    "Spellings": "  |  ".join(
                        f"{m.name or '(no name)'} [{m.code or '-'}]" for m in g.members
                    ),
                    "Rows": g.row_count,
                    "Why": g.comment,
                }
                for g in artifact.groups
            ]
        ),
        key="review_companies",
        width="stretch",
        hide_index=True,
        disabled=["Spellings", "Rows", "Why"],
    )
    approvals = {g.group_id: bool(v) for g, v in zip(artifact.groups, edited["Same company"])}
    names = {g.group_id: str(n) for g, n in zip(artifact.groups, edited["Canonical name"])}
    return approvals, names


def _suppliers(run_id, artifact):
    groups = artifact.groups
    ic_groups = [g for g in groups if g.is_intercompany]

    st.subheader(f"3 · Intercompany ({len(ic_groups)})")
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

    st.subheader("4 · Supplier consolidation")
    assignments = _consolidation(run_id, artifact)
    return intercompany, assignments


# What decided a name's group, in the words of the screen rather than the code.
DECIDED_BY = {
    "deterministic": "cleanup match",
    "ai": "agent",
    "ai_unsure": "agent unsure",
    "user": "you",
}


def _consolidation(run_id, artifact) -> dict[str, str]:
    """Every raw name, the group it belongs to, and who decided that.

    One table rather than four blocks. The count in the heading is then something
    a reader can add up, and correcting the agent is the same gesture whether it
    merged two names or kept them apart: type the group you want.
    """
    groups = artifact.groups
    volumes = name_volumes(run_id)
    # A group the agent was unsure about is not merged unless someone says so, so
    # its members start under their own names -- the same default as before.
    initial = {
        member: (group.canonical_name if group.approved else member)
        for group in groups
        for member in group.members
    }
    if not initial:
        st.info("No supplier names to group.")
        return {}

    unsure = sum(1 for g in groups if not g.approved for _ in g.members)
    st.caption(
        f"{artifact.distinct_names} raw names as the export wrote them. Type in **Group** to "
        "move a name, invent a group or split one; clear the cell and the name stands alone. "
        "Sort by Group to bring a group's names together."
        + (
            f"  \n**{unsure} name(s) the agent was not confident about are left ungrouped** — "
            "sort by *Decided by* to find them."
            if unsure
            else ""
        )
    )

    apart = _kept_apart(artifact)
    edited = st.data_editor(
        as_money(
            pd.DataFrame(
                [
                    {
                        "Raw name": name,
                        "Group": group,
                        "Rows": int(volumes["rows"].get(name, 0)),
                        "Spend (EUR)": float(volumes["spend"].get(name, 0.0)),
                        "Decided by": _decided_by(name, groups),
                        "Note": _note(name, groups, apart),
                    }
                    for name, group in sorted(initial.items(), key=lambda i: (i[1], i[0]))
                ]
            ),
            "Spend (EUR)",
        ),
        key="review_consolidation",
        width="stretch",
        hide_index=True,
        disabled=["Raw name", "Rows", "Spend (EUR)", "Decided by", "Note"],
        column_config={"Spend (EUR)": money()},
    )

    assignments = {
        str(name): str(group) for name, group in zip(edited["Raw name"], edited["Group"])
    }
    resulting, own = _resulting(assignments, groups, volumes)
    proposed, _ = _resulting(initial, groups, volumes)
    st.caption(
        f"{len(assignments)} names → **{resulting} suppliers**, {own} of them the group's "
        "own entities (block 3)."
        + (f"  As proposed: {proposed}." if assignments != initial else "")
    )
    return assignments


def _resulting(assignments: dict[str, str], groups, volumes) -> tuple[int, int]:
    """How many suppliers a set of assignments produces, and how many are the group's own.

    The intercompany mark follows whichever original group contributes the most
    rows, so the count here is arrived at the same way confirm_suppliers does it
    -- a preview that disagreed with the result would be worse than none.
    """
    origin = {member: group for group in groups for member in group.members}
    clusters: dict[str, list[str]] = {}
    for name, label in assignments.items():
        clusters.setdefault(label.strip() or name, []).append(name)

    own = 0
    for members in clusters.values():
        sources = [origin[name] for name in members if name in origin]
        if not sources:
            continue
        dominant = max(
            sources,
            key=lambda g: sum(int(volumes["rows"].get(n, 0)) for n in g.members),
        )
        own += bool(dominant.is_intercompany)
    return len(clusters), own


def _kept_apart(artifact) -> dict[str, list[str]]:
    """Per name, the neighbours the agent judged to be a different supplier.

    This used to be a read-only list of its own. On the row it belongs to it is
    actionable: give both names the same group and the agent is overruled.
    """
    apart: dict[str, list[str]] = {}
    for pair in artifact.rejected:
        apart.setdefault(pair.left, []).append(pair.right)
        apart.setdefault(pair.right, []).append(pair.left)
    return apart


def _decided_by(name: str, groups) -> str:
    group = next(g for g in groups if name in g.members)
    if len(group.members) == 1:
        return "alone"
    return DECIDED_BY.get(group.source, group.source)


def _note(name: str, groups, apart: dict[str, list[str]]) -> str:
    group = next(g for g in groups if name in g.members)
    parts = []
    if len(group.members) > 1:
        parts.append(group.comment)
    if name in apart:
        parts.append("agent kept it apart from: " + ", ".join(sorted(apart[name])))
    return " · ".join(parts) or "No other name comes close."


def _currency(run_id: str) -> None:
    report = load_currency(run_id)
    st.subheader("5 · Currencies")
    st.caption(
        "Converted at the ECB daily reference rate of each posting date. Nothing to decide "
        "here unless a currency is missing a rate."
    )
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

    st.subheader(f"6 · Addressable spend ({len(artifact.cost_types)} cost types)")
    st.caption(
        "Payroll, taxes, interest and provisions sit in the same ledger as consulting and "
        "freight, but procurement cannot negotiate them. Untick what it cannot influence."
    )

    # One hedged answer here moves the addressable figure by millions, and the
    # table sorts by spend rather than by how sure the agent was. Named up front,
    # the cases worth a second look cannot be scrolled past.
    unsure = [c for c in artifact.cost_types if c.confidence < CONFIDENCE_THRESHOLD]
    if unsure:
        st.warning(
            f"**{len(unsure)} cost type(s) the agent was unsure about, together "
            f"{eur(sum(c.spend for c in unsure))} EUR.** "
            + " · ".join(
                f"{c.cost_type} ({eur(c.spend)}, "
                f"{'addressable' if c.addressable else 'not addressable'}, "
                f"confidence {c.confidence:.2f})"
                for c in sorted(unsure, key=lambda c: -c.spend)
            )
        )

    edited = st.data_editor(
        as_money(
            pd.DataFrame(
                [
                    {
                        "Addressable": c.addressable,
                        "Cost type": c.cost_type,
                        "Spend (EUR)": c.spend,
                        "Rows": c.rows,
                        "Confidence": c.confidence,
                        "Why": c.comment,
                    }
                    for c in artifact.cost_types
                ]
            ),
            "Spend (EUR)",
        ),
        key="review_addressability",
        width="stretch",
        hide_index=True,
        disabled=["Cost type", "Spend (EUR)", "Rows", "Confidence", "Why"],
        column_config={"Spend (EUR)": money(), "Confidence": rate_column()},
    )
    return {c.cost_type: bool(v) for c, v in zip(artifact.cost_types, edited["Addressable"])}


def _apply(
    run_id,
    excluded,
    company_approvals,
    company_names,
    intercompany,
    assignments,
    addressable,
) -> None:
    """Apply every decision, then recompute what depends on it. No model calls here."""
    with st.status("Applying your decisions", expanded=True) as status:
        st.write("Recording total rows and category usability")
        confirm_profiling(run_id, excluded=excluded)

        if has_companies(run_id):
            # Before the rule engine: duplicate detection keys on the canonical
            # company, so the companies have to be settled first.
            st.write("Writing canonical company names")
            confirm_companies(run_id, company_approvals, company_names)

        st.write("Re-flagging rows")
        run_rule_engine(run_id)

        st.write("Writing canonical suppliers and intercompany")
        confirm_suppliers(run_id, intercompany=intercompany, assignments=assignments)

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
