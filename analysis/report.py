"""What a report shows, assembled once for every way of showing it.

The screen, the workbook and the HTML file are three renderings of one document,
not three collections of the same facts. Assembled separately they would agree
today and drift at the first new chart, so the order of the sections, the choice
of figures and the tables underneath them live here, and each renderer only
decides how to draw them.

Nothing is computed in this module. Every figure comes from a stage artifact, from
spend_chain(), or from analysis.views -- the same sources the screens already read.
"""

from dataclasses import dataclass, field, replace
from datetime import date

import pandas as pd

from analysis import charts, views
from analysis.charts import Figure
from analysis.spend_report import spend_chain
from core.canonical import company_key
from levers.definitions import BY_ID, SPEND_KINDS
from suppliers.candidates import normalize_name
from suppliers.intercompany import group_stem

ASSUMPTION_NOTE = (
    "Saving percentages are assumptions, not findings. The spend each lever applies "
    "to, which bookings those are and how they were assigned come from the data; "
    "only the rate is assumed."
)

# The dropdown on the Visuals tab is a screen affordance; a document shows the
# same figures side by side. Both read this block, which is why it has a name.
BY_COMPANY = "by_company"

MAX_COMPANY_FIGURES = 8


@dataclass(frozen=True)
class Block:
    """One titled unit of a report: figures, a table, key figures, or prose."""

    title: str
    caption: str = ""
    metrics: tuple[tuple[str, str], ...] = ()
    table: pd.DataFrame | None = None
    figures: tuple[Figure, ...] = ()
    labels: tuple[str, ...] = ()  # one per figure, where they need naming
    body: str = ""
    block_id: str = ""


@dataclass(frozen=True)
class Section:
    title: str
    blocks: list[Block] = field(default_factory=list)


@dataclass(frozen=True)
class Cover:
    """Who the analysis is about and what it rests on. Nothing here is invented."""

    title: str
    group: str
    run_id: str
    prepared_on: date
    sources: tuple[str, ...]
    rows_total: int
    rows_analysed: int
    metrics: tuple[tuple[str, str], ...]
    note: str = ASSUMPTION_NOTE


@dataclass(frozen=True)
class ReportDocument:
    cover: Cover
    sections: list[Section]


def build_report(run_id: str) -> ReportDocument:
    """The whole document. Sections a run has no artifact for are left out."""
    from analysis.summary import has_summary, load_summary
    from core.table import has_table, load_table
    from levers.engine import has_artifact, load_artifact

    table = load_table(run_id) if has_table(run_id) else pd.DataFrame()
    summary = load_summary(run_id) if has_summary(run_id) else None
    artifact = load_artifact(run_id) if has_artifact(run_id) else None

    sections = [
        section
        for section in (
            _overview(summary),
            _top_levers(artifact),
            _catalogue(artifact),
            _visuals(run_id, table, artifact),
            _questions(summary, artifact),
        )
        if section is not None and section.blocks
    ]
    return ReportDocument(cover=_cover(run_id, table, artifact), sections=sections)


# --- the cover -------------------------------------------------------------


def _cover(run_id: str, table: pd.DataFrame, artifact) -> Cover:
    from triage.workbook_triage import has_confirmed, load_confirmed_triage

    sources: tuple[str, ...] = ()
    if has_confirmed(run_id):
        sources = tuple(w.original_filename for w in load_confirmed_triage(run_id).workbooks)

    analysed = 0
    if not table.empty and "include_spend_analysis" in table.columns:
        analysed = int(table["include_spend_analysis"].astype(bool).sum())

    metrics: tuple[tuple[str, str], ...] = ()
    if artifact:
        metrics = (
            ("Analysable spend", f"{artifact.analysable_spend:,.0f} EUR"),
            ("Identified potential", f"{artifact.total_base:,.0f} EUR"),
            (
                "Range",
                f"{artifact.total_low:,.0f} – {artifact.total_high:,.0f} EUR",
            ),
        )

    return Cover(
        title="Procurement Lever Analysis",
        group=group_name(table),
        run_id=run_id,
        prepared_on=date.today(),
        sources=sources,
        rows_total=len(table),
        rows_analysed=analysed,
        metrics=metrics,
    )


def group_name(table: pd.DataFrame) -> str:
    """The group's own name, from the tokens its company names share.

    Derived the same way intercompany detection derives it, so the cover names
    the group the data names -- nothing is configured and nothing is guessed.
    """
    if table.empty or "company_name" not in table.columns:
        return "Portfolio company"
    companies = sorted({c for c in company_key(table).astype(str) if c.strip()})
    if not companies:
        return "Portfolio company"

    stem = group_stem(companies)
    if stem:
        # Keep the order the tokens have in the longest name, not set order.
        longest = max(companies, key=len)
        ordered = [t for t in normalize_name(longest).split() if t in stem]
        if ordered:
            return " ".join(word.capitalize() for word in ordered)
    return companies[0]


# --- the sections ----------------------------------------------------------


def _overview(summary) -> Section | None:
    if summary is None:
        return None
    return Section(
        title="Overview",
        blocks=[
            Block(
                title=section.title,
                caption=section.headline,
                metrics=tuple(tuple(m) for m in section.metrics),
                table=pd.DataFrame(section.rows) if section.rows else None,
                body="\n".join(f"- {fact}" for fact in section.facts),
            )
            for section in summary.sections
        ],
    )


def _top_levers(artifact) -> Section | None:
    if artifact is None:
        return None
    quantified = [
        l for l in artifact.levers if l.status == "quantified" and l.kind in SPEND_KINDS
    ]
    if not quantified:
        return None

    blocks = []
    for rank, lever in enumerate(quantified[:3], start=1):
        steps = "\n".join(f"- {step}" for step in lever.next_steps)
        blocks.append(
            Block(
                title=f"{rank} · {lever.name}",
                caption=(
                    f"{lever.net_base:,.0f} EUR × {lever.rate_base:.0%} assumed = "
                    f"{lever.potential_base:,.0f} EUR · confidence {lever.confidence}"
                ),
                metrics=(
                    ("Potential (base)", f"{lever.potential_base:,.0f} EUR"),
                    (
                        "Range",
                        f"{lever.potential_low:,.0f} – {lever.potential_high:,.0f} EUR",
                    ),
                    ("Applies to", f"{lever.net_base:,.0f} EUR"),
                ),
                table=_contributor_table(lever),
                body="\n\n".join(part for part in (lever.opportunity, steps) if part),
            )
        )

    rest = quantified[3:]
    if rest:
        blocks.append(
            Block(
                title="The remaining levers",
                table=pd.DataFrame(
                    [
                        {
                            "Lever": l.name,
                            "Applies to (EUR)": l.net_base,
                            "Rate": f"{l.rate_base:.0%}",
                            "Potential (EUR)": l.potential_base,
                        }
                        for l in rest
                    ]
                ),
            )
        )
    return Section(title="Top Levers", blocks=blocks)


def _catalogue(artifact) -> Section | None:
    if artifact is None:
        return None

    quantified = [l for l in artifact.levers if l.status == "quantified" and l.kind != "risk"]
    risks = [l for l in artifact.levers if l.status == "quantified" and l.kind == "risk"]
    absent = [l for l in artifact.levers if l.status == "not_applicable"]
    blocked = [l for l in artifact.levers if l.status == "not_assessable"]

    blocks = []
    if quantified:
        blocks.append(
            Block(
                title="Priority",
                caption=(
                    "Ranked by potential in the base case. Every euro counts towards one "
                    "lever only, so the bases add up to the analysable spend."
                ),
                table=pd.DataFrame(
                    [
                        {
                            "#": rank,
                            "Lever": l.name,
                            "Applies to (EUR)": l.net_base,
                            "Rate": f"{l.rate_base:.0%}",
                            "Potential (EUR)": l.potential_base,
                            "Range": f"{l.potential_low:,.0f} – {l.potential_high:,.0f}",
                            "Effort": l.effort,
                            "Confidence": l.confidence,
                        }
                        for rank, l in enumerate(quantified, start=1)
                    ]
                    # A workbook should be able to check itself, so the figure the
                    # cover quotes stands under the rows it is made of.
                    + [
                        {
                            "#": "",
                            "Lever": "Total",
                            "Applies to (EUR)": sum(l.net_base for l in quantified),
                            "Rate": "",
                            "Potential (EUR)": artifact.total_base,
                            "Range": f"{artifact.total_low:,.0f} – {artifact.total_high:,.0f}",
                            "Effort": "",
                            "Confidence": "",
                        }
                    ]
                ),
            )
        )
    if risks:
        blocks.append(
            Block(
                title="Risk exposures",
                caption="Measured, but an exposure rather than a saving. They claim no euros.",
                table=pd.DataFrame(
                    [{"Lever": l.name, "Exposure (EUR)": l.gross_base, "Finding": l.metric}
                     for l in risks]
                ),
            )
        )
    if absent:
        blocks.append(
            Block(
                title="Tested and empty",
                caption="Measurable from this data, and the measurement found nothing.",
                table=pd.DataFrame(
                    [{"Lever": l.name, "Why": l.status_reason} for l in absent]
                ),
            )
        )
    if blocked:
        blocks.append(
            Block(
                title="Not assessable from this data",
                caption=(
                    "A zero here would be a claim the data cannot support. Each names the "
                    "field that would settle it."
                ),
                table=pd.DataFrame(
                    [
                        {
                            "Lever": l.name,
                            "Missing": ", ".join(l.missing_fields),
                            "Why": l.status_reason,
                        }
                        for l in blocked
                    ]
                ),
            )
        )
    if artifact.benchmark:
        blocks.append(
            Block(
                title="The companies compared",
                caption="Sorted by the share of spend without a contract on file.",
                table=pd.DataFrame(
                    [
                        {
                            "Company": e.company,
                            "Spend (EUR)": e.spend,
                            "Suppliers": e.suppliers,
                            "PO coverage": f"{e.po_coverage:.1%}",
                            "Without contract": f"{e.uncontracted_share:.1%}",
                        }
                        for e in artifact.benchmark
                    ]
                ),
            )
        )
    return Section(title="All Levers", blocks=blocks)


def _visuals(run_id: str, table: pd.DataFrame, artifact) -> Section | None:
    blocks = visual_blocks(table, artifact)
    return Section(title="Visuals", blocks=blocks) if blocks else None


def visual_blocks(table: pd.DataFrame, artifact) -> list[Block]:
    """The figures, in the order they are read. Shared by the screen and both exports."""
    if table.empty:
        return []

    blocks: list[Block] = []
    if "include_spend_analysis" in table.columns:
        chain = [
            {"label": s.label, "amount": s.amount, "delta": s.delta}
            for s in spend_chain(table).chain
        ]
        blocks.append(
            Block(
                title="From booked to negotiable",
                caption=(
                    "Each step names its own population rather than subtracting loosely. "
                    "The last figure is what every lever is measured against."
                ),
                figures=(charts.spend_waterfall(chain),),
            )
        )

    rows = views.addressable(table)
    if rows.empty:
        return blocks

    spend = views.supplier_spend(rows)
    blocks.append(
        Block(
            title="Where the money goes",
            figures=(charts.supplier_share(spend), charts.supplier_ranking(spend)),
            labels=("Share of spend", "Largest suppliers"),
        )
    )

    company = company_key(rows)
    companies = sorted(company.astype(str).unique())[:MAX_COMPANY_FIGURES]
    if companies:
        blocks.append(
            Block(
                title="By company",
                block_id=BY_COMPANY,
                figures=tuple(_company_figure(rows[company == name]) for name in companies),
                labels=tuple(companies),
            )
        )

    blocks.append(
        Block(
            title="Spend over the year",
            figures=(charts.monthly_spend(views.monthly_spend(rows)),),
        )
    )
    blocks.append(
        Block(
            title="Contract coverage by company",
            figures=(charts.contract_coverage(views.contract_coverage(rows)),),
        )
    )
    if artifact:
        names = {key: lever.name for key, lever in BY_ID.items()}
        blocks.append(
            Block(
                title="How the spend divides across levers",
                caption="Each euro is credited to exactly one lever, so these add up to the whole.",
                figures=(charts.lever_allocation(views.lever_allocation(rows, names)),),
            )
        )
    return blocks


def _questions(summary, artifact) -> Section | None:
    blocks = []
    if artifact and artifact.data_requests:
        blocks.append(
            Block(
                title="Data to request",
                caption=(
                    "Each of these is a field the mapping already looks for. Supplying it "
                    "turns a lever that cannot be assessed into a measured figure."
                ),
                table=pd.DataFrame(
                    [
                        {"Field": r.label, "Would unlock": ", ".join(r.unlocks)}
                        for r in artifact.data_requests
                    ]
                ),
            )
        )
    if summary and summary.sme_questions:
        blocks.append(
            Block(
                title="Questions for the business",
                caption="Raised by the findings, to be answered by the people who booked them.",
                table=pd.DataFrame(
                    [
                        {
                            "Question": q.question,
                            "Ask": q.addressee,
                            "Why": q.rationale,
                            "Would settle": q.unlocks,
                        }
                        for q in summary.sme_questions
                    ]
                ),
            )
        )
    return Section(title="Open Questions", blocks=blocks) if blocks else None


def _company_figure(subset: pd.DataFrame) -> Figure:
    """One company's supplier split, captioned so the figure stands on its own."""
    figure = charts.supplier_share(views.supplier_spend(subset))
    return replace(
        figure,
        caption=(
            f"{subset['amount_eur'].sum():,.0f} EUR across "
            f"{subset['supplier_normalized'].nunique()} suppliers"
        ),
    )


def _contributor_table(lever) -> pd.DataFrame | None:
    if not lever.contributors:
        return None
    return pd.DataFrame(
        [
            {
                "Supplier": c.supplier,
                "Spend (EUR)": c.spend,
                "Companies": c.companies,
                "Bookings": c.rows,
                "Contract": c.contract_status or "-",
            }
            for c in lever.contributors
        ]
    )
