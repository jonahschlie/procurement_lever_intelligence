"""Executive summary: the result for someone who did not click through the analysis.

Six tabs over the artifacts the pipeline already wrote. Nothing is recomputed
here -- the figures come from the stages, so this view can never disagree with the
screens behind it.
"""

import pandas as pd
import streamlit as st

from agents.analysis_chat import ChatAnswer
from agents.analysis_chat import build_input as chat_input
from agents.analysis_chat import definition as chat_definition
from agents.base import run_agent
from analysis import charts, views
from analysis.spend_report import build_spend_report
from analysis.summary import analysis_context, has_summary, load_summary
from core.canonical import company_key
from core.table import has_table, load_table
from levers.definitions import BY_ID, SPEND_KINDS
from levers.engine import has_artifact, load_artifact
from ui import levers as levers_page
from ui.format import as_money, eur, eur_compact, money, percent

ASSUMPTION_NOTE = (
    "**Saving percentages are assumptions, not findings.** The spend each lever "
    "applies to, which bookings those are and how they were assigned come from the "
    "data; only the rate is assumed."
)

STARTER_QUESTIONS = (
    "What are the three biggest opportunities and why?",
    "Why is the category analysis switched off?",
    "How reliable are these numbers?",
)


def render() -> None:
    st.title("Executive Summary")

    run_id = st.session_state.get("run_id")
    if run_id is None or not has_summary(run_id):
        st.info("No summary yet. Identify the levers first, then build it there.")
        return

    summary = load_summary(run_id)
    table = load_table(run_id) if has_table(run_id) else pd.DataFrame()
    artifact = load_artifact(run_id) if has_artifact(run_id) else None

    overview, top, catalogue, visuals, questions, chat = st.tabs(
        ["Overview", "Top Levers", "All Levers", "Visuals", "Open Questions", "Ask the Analysis"]
    )
    with overview:
        _overview(summary, artifact, table)
    with top:
        _top_levers(artifact)
    with catalogue:
        _catalogue(artifact, table)
    with visuals:
        _visuals(table, artifact)
    with questions:
        _questions(summary, artifact)
    with chat:
        _chat(run_id, table)


def _overview(summary, artifact, table) -> None:
    if artifact:
        left, middle, right, far = st.columns(4)
        left.metric(
            "Analysable spend (EUR)",
            eur_compact(artifact.analysable_spend),
            help=f"{eur(artifact.analysable_spend)} — addressable spend less the bookings "
            "that name no supplier",
        )
        middle.metric(
            "Identified potential (EUR)",
            eur_compact(artifact.total_base),
            help=eur(artifact.total_base),
        )
        right.metric(
            "Range",
            f"{eur_compact(artifact.total_low)} – {eur_compact(artifact.total_high)}",
            help=f"{eur(artifact.total_low)} to {eur(artifact.total_high)}",
        )
        far.metric("Rows analysed", f"{len(table):,}")
        st.caption(ASSUMPTION_NOTE)

    st.divider()
    for section in summary.sections:
        st.subheader(section.title)
        # The headline carries the meaning; the metrics and the table carry the
        # evidence. Figures without the sentence read impressively and say nothing.
        st.markdown(f"**{section.headline}**")
        _section_metrics(section)
        _section_table(section)
        for fact in section.facts:
            st.markdown(f"- {fact}")


def _section_metrics(section) -> None:
    if not section.metrics:
        return
    for column, (label, value) in zip(st.columns(len(section.metrics)), section.metrics):
        column.metric(label, value)


# A section column carrying an amount says so in its label, so one rule covers
# every section without the renderer knowing what any of them contain.
AMOUNT_SUFFIXES = ("(EUR)", "(local)")


def _section_table(section) -> None:
    """A section's detail, with amount columns shown as amounts."""
    if not section.rows:
        return
    frame = pd.DataFrame(section.rows)
    amounts = [
        column for column in frame.columns if str(column).endswith(AMOUNT_SUFFIXES)
    ]
    st.dataframe(
        as_money(frame, *amounts),
        width="stretch",
        hide_index=True,
        column_config={column: money() for column in amounts},
    )


def _top_levers(artifact) -> None:
    if artifact is None:
        st.info("No levers identified yet.")
        return

    quantified = [
        l for l in artifact.levers if l.status == "quantified" and l.kind in SPEND_KINDS
    ]
    if not quantified:
        st.warning("No lever could be quantified from this data.")
        return

    st.warning(ASSUMPTION_NOTE)

    for rank, lever in enumerate(quantified[:3], start=1):
        with st.container(border=True):
            st.subheader(f"{rank} · {lever.name}")
            left, middle, right = st.columns(3)
            left.metric("Potential (base)", eur(lever.potential_base))
            middle.metric(
                "Range", f"{eur_compact(lever.potential_low)} – {eur_compact(lever.potential_high)}"
            )
            right.metric("Applies to", eur(lever.net_base))
            st.caption(
                f"{eur(lever.net_base)} EUR × {lever.rate_base:.0%} assumed = "
                f"{eur(lever.potential_base)} EUR · confidence {lever.confidence}"
            )

            if lever.opportunity:
                st.markdown(lever.opportunity)
            if lever.next_steps:
                for step in lever.next_steps:
                    st.markdown(f"- {step}")
            if lever.contributors:
                st.caption(
                    "Largest contributors: "
                    + ", ".join(
                        f"{c.supplier} ({eur(c.spend)} EUR)" for c in lever.contributors[:3]
                    )
                )

    rest = quantified[3:]
    if rest:
        st.subheader("The remaining levers")
        st.dataframe(
            as_money(
                pd.DataFrame(
                    [
                        {
                            "Lever": l.name,
                            "Applies to (EUR)": l.net_base,
                            "Potential (base)": l.potential_base,
                            "Rate": f"{l.rate_base:.0%}",
                        }
                        for l in rest
                    ]
                ),
                "Applies to (EUR)",
                "Potential (base)",
            ),
            width="stretch",
            hide_index=True,
            column_config={"Applies to (EUR)": money(), "Potential (base)": money()},
        )

    if artifact.priority_rationale:
        st.markdown("**Why this order**")
        st.markdown(artifact.priority_rationale)


def _catalogue(artifact, table) -> None:
    """The full catalogue, rendered by the lever page so there is one version."""
    if artifact is None:
        st.info("No levers identified yet.")
        return

    quantified = [l for l in artifact.levers if l.status == "quantified" and l.kind != "risk"]
    risks = [l for l in artifact.levers if l.status == "quantified" and l.kind == "risk"]
    absent = [l for l in artifact.levers if l.status == "not_applicable"]
    blocked = [l for l in artifact.levers if l.status == "not_assessable"]

    levers_page._priority(artifact, quantified)
    for rank, lever in enumerate(quantified, start=1):
        levers_page._lever(rank, lever, table)
    levers_page._risks(risks)
    levers_page._absent(absent)
    levers_page._blocked(blocked, artifact)
    levers_page._benchmark(artifact)
    levers_page._assumptions(artifact)


def _visuals(table: pd.DataFrame, artifact) -> None:
    if table.empty:
        st.info("No table to visualise yet.")
        return

    rows = views.addressable(table)
    if "include_spend_analysis" in table.columns:
        report = build_spend_report(st.session_state["run_id"])
        st.subheader("From booked to negotiable")
        chain = [{"label": s.label, "amount": s.amount, "delta": s.delta} for s in report.chain]
        _figure(charts.spend_waterfall(chain))

    if rows.empty:
        st.info("No addressable spend to chart yet.")
        return

    st.subheader("Where the money goes")
    spend = views.supplier_spend(rows)
    left, right = st.columns([1, 1])
    with left:
        _figure(charts.supplier_share(spend), key="share")
    with right:
        _figure(charts.supplier_ranking(spend), key="ranking")

    st.subheader("By company")
    company = company_key(rows)
    companies = sorted(company.astype(str).unique())
    if companies:
        chosen = st.selectbox("Company", companies)
        subset = rows[company == chosen]
        st.caption(
            f"{eur(subset['amount_eur'].sum())} EUR across "
            f"{subset['supplier_normalized'].nunique()} suppliers"
        )
        _figure(charts.supplier_share(views.supplier_spend(subset)), key="company")

    st.subheader("Spend over the year")
    _figure(charts.monthly_spend(views.monthly_spend(rows)), key="monthly")

    st.subheader("Contract coverage by company")
    _figure(charts.contract_coverage(views.contract_coverage(rows)), key="contracts")

    if artifact:
        st.subheader("How the spend divides across levers")
        st.caption("Each euro is credited to exactly one lever, so these add up to the whole.")
        names = {key: lever.name for key, lever in BY_ID.items()}
        _figure(charts.lever_allocation(views.lever_allocation(rows, names)), key="allocation")


def _figure(figure, key: str = "") -> None:
    """A chart with the numbers behind it, as the guideline requires."""
    st.altair_chart(figure.chart, width="stretch")
    if not figure.data.empty:
        with st.expander("Show data"):
            st.dataframe(
                as_money(figure.data, *_amount_columns(figure.data)),
                width="stretch",
                hide_index=True,
                column_config=_data_config(figure.data),
            )


# Chart frames carry amounts and shares side by side; shares are already
# fractions of one, everything else numeric is euros.
SHARE_COLUMNS = ("share", "cumulative")


def _amount_columns(frame: pd.DataFrame) -> list[str]:
    return [
        column
        for column in frame.columns
        if column not in SHARE_COLUMNS and pd.api.types.is_numeric_dtype(frame[column])
    ]


def _data_config(frame: pd.DataFrame) -> dict:
    config = {column: money() for column in _amount_columns(frame)}
    config.update(
        {column: percent() for column in SHARE_COLUMNS if column in frame.columns}
    )
    return config


def _questions(summary, artifact) -> None:
    if artifact and artifact.data_requests:
        st.subheader("Data to request")
        st.caption(
            "Each of these is a field the mapping already looks for. Supplying it turns "
            "a lever that cannot be assessed into a measured figure."
        )
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

    st.subheader("Questions for the business")
    if not summary.sme_questions:
        st.info("No questions were generated for this run.")
        return
    st.caption(
        "The data measures what happened; it cannot say why. These are the questions "
        "the findings raise for the people who know the business."
    )
    for entry in summary.sme_questions:
        with st.container(border=True):
            st.markdown(f"**{entry.question}**")
            st.caption(f"Ask: {entry.addressee}  ·  Because: {entry.rationale}")
            st.caption(f"Would let us: {entry.unlocks}")


def _chat(run_id: str, table: pd.DataFrame) -> None:
    st.caption(
        "Answers come from this analysis only. Anything it does not cover, the "
        "assistant will say so rather than estimate."
    )

    history = st.session_state.setdefault("chat_history", [])
    for turn in history:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])
            if turn.get("source"):
                st.caption(f"From: {turn['source']}")

    asked = None
    if not history:
        columns = st.columns(len(STARTER_QUESTIONS))
        for column, question in zip(columns, STARTER_QUESTIONS):
            if column.button(question, key=f"starter_{question[:20]}"):
                asked = question

    typed = st.chat_input("Ask about this analysis")
    question = asked or typed
    if not question:
        return

    history.append({"role": "user", "content": question})
    context = _context(run_id)
    with st.spinner("Reading the analysis"):
        try:
            result = run_agent(chat_definition(context), chat_input(history[:-1], question))
            answer: ChatAnswer = result.output
            history.append(
                {
                    "role": "assistant",
                    "content": answer.answer,
                    "source": "" if answer.outside_analysis else answer.source,
                }
            )
        except Exception as error:
            history.append(
                {"role": "assistant", "content": f"Could not answer: {error}", "source": ""}
            )
    st.rerun()


@st.cache_data(show_spinner=False)
def _context(run_id: str) -> dict:
    """Built once per run: aggregates only, never a booking."""
    return analysis_context(run_id)
