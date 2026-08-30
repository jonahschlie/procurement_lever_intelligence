"""Assembling the executive summary from what the stages already produced.

One place gathers the findings so the summary screens and, later, the export read
the same facts. Nothing is recomputed here: every figure comes from a stage
artifact or from spend_chain(), which stays the single source of the spend maths.

Every section degrades on its own. A run that stopped after profiling produces a
shorter summary rather than an error.
"""

from pathlib import Path

import pandas as pd

from agents.base import run_agent
from agents.sme_questions import build_input, definition
from analysis.spend_report import spend_chain
from core.models import (
    ExecutiveSummary,
    LlmCall,
    SmeQuestionRecord,
    SummarySection,
)
from core.run import get_logger, record_step, step_path
from core.table import has_table, load_table

STEP = "executive_summary"
ARTIFACT_NAME = "summary.json"
MAX_FACTS = 6


def build_summary(run_id: str, *, client=None) -> ExecutiveSummary:
    logger = get_logger(run_id)
    table = load_table(run_id) if has_table(run_id) else pd.DataFrame()

    sections = [
        section
        for section in (
            _ingestion(run_id),
            _mapping(run_id),
            _quality(run_id, table),
            _currency(run_id),
            _suppliers(run_id, table),
            _levers(run_id),
        )
        if section is not None
    ]
    summary = ExecutiveSummary(run_id=run_id, sections=sections)
    summary = _add_questions(run_id, summary, table, client, logger)

    target = step_path(run_id, STEP)
    (target / ARTIFACT_NAME).write_bytes(summary.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [target / ARTIFACT_NAME])
    logger.info(
        "executive summary built: %d section(s), %d question(s)",
        len(summary.sections),
        len(summary.sme_questions),
    )
    return summary


def load_summary(run_id: str) -> ExecutiveSummary:
    path = step_path(run_id, STEP) / ARTIFACT_NAME
    return ExecutiveSummary.model_validate_json(path.read_bytes())


def has_summary(run_id: str) -> bool:
    return (step_path(run_id, STEP) / ARTIFACT_NAME).is_file()


# --- the sections ----------------------------------------------------------


def _ingestion(run_id: str) -> SummarySection | None:
    from triage.workbook_triage import has_confirmed, load_confirmed_triage

    if not has_confirmed(run_id):
        return None
    artifact = load_confirmed_triage(run_id)
    facts = []
    for workbook in artifact.workbooks:
        roles = ", ".join(
            f"{entry.sheet or 'single table'} ({entry.role})" for entry in workbook.classifications
        )
        facts.append(f"{workbook.original_filename}: {roles}")
    datasets = artifact.datasets
    return SummarySection(
        title="What was submitted",
        headline=(
            f"{len(artifact.workbooks)} file(s), {len(datasets)} dataset(s) worth analysing"
        ),
        facts=facts[:MAX_FACTS],
    )


def _mapping(run_id: str) -> SummarySection | None:
    from core.canonical import CANONICAL_FIELDS, field_by_key
    from mapping.schema_mapping import has_confirmed, load_confirmed

    if not has_confirmed(run_id):
        return None
    artifact = load_confirmed(run_id)
    if not artifact.datasets:
        return None

    dataset = artifact.datasets[0]
    mapped = [m for m in dataset.mappings if m.source_column]
    unmapped = [field_by_key(m.canonical_field).label for m in dataset.mappings if not m.source_column]
    by_user = sum(1 for m in dataset.mappings if m.decided_by == "user")

    facts = [f"{len(mapped)} of {len(CANONICAL_FIELDS)} canonical fields matched to a column"]
    if unmapped:
        facts.append("Left empty: " + ", ".join(unmapped[:8]))
    if by_user:
        facts.append(f"{by_user} field(s) corrected by hand")
    return SummarySection(
        title="How the columns were understood",
        headline=f"{len(mapped)} fields mapped, {len(unmapped)} left empty",
        facts=facts,
    )


def _quality(run_id: str, table: pd.DataFrame) -> SummarySection | None:
    from profiling.data_profiling import has_report, load_report

    if not has_report(run_id):
        return None
    report = load_report(run_id)
    serious = [f for f in report.findings if f.severity == "high"]
    excluded = sum(1 for c in report.aggregate_candidates if c.exclude)

    facts = [f"{f.check}: {f.result}" for f in serious[:MAX_FACTS]]
    if excluded:
        facts.append(f"{excluded} total row(s) excluded from spend, flagged not deleted")
    facts.append(report.category_decision)
    return SummarySection(
        title="What the data quality checks found",
        headline=f"{len(report.findings)} findings, {len(serious)} of them serious",
        facts=facts,
    )


def _currency(run_id: str) -> SummarySection | None:
    from fx.currency import has_report, load_report

    if not has_report(run_id):
        return None
    report = load_report(run_id)
    currencies = ", ".join(entry.currency for entry in report.breakdown)
    facts = [
        f"Currencies in the data: {currencies}",
        f"Converted at {report.rate_source} daily rates ({report.rates_frozen_to}), frozen into the run",
        f"Gross {report.spend_gross_eur:,.0f} EUR less {report.credit_volume_eur:,.0f} in credit notes",
    ]
    if report.group_unconverted_rows:
        facts.append(
            f"{report.group_unconverted_rows:,} rows carried a group amount that was never "
            "converted in the source; used as a cross-check only"
        )
    return SummarySection(
        title="What the currency conversion changed",
        headline=f"Net spend {report.spend_net_eur:,.0f} EUR",
        facts=facts,
    )


def _suppliers(run_id: str, table: pd.DataFrame) -> SummarySection | None:
    from suppliers.normalization import has_confirmed, load_confirmed

    if not has_confirmed(run_id):
        return None
    artifact = load_confirmed(run_id)
    approved = [g for g in artifact.groups if g.approved]
    intercompany = [g for g in approved if g.is_intercompany]
    by_user = sum(1 for g in artifact.groups if g.source == "user")

    facts = [
        f"{artifact.distinct_names} raw names resolved to {len(approved)} suppliers",
        f"{len(intercompany)} of them are the group's own entities",
    ]
    if by_user:
        facts.append(f"{by_user} grouping(s) decided by hand")
    if not table.empty and "supplier_contract_status" in table.columns:
        eligible = table[table["include_supplier_analysis"].astype(bool)]
        eligible = eligible[eligible["amount_eur"].notna()]
        if not eligible.empty:
            total = eligible["amount_eur"].sum() or 1
            without = eligible.loc[
                eligible["supplier_contract_status"] == "no", "amount_eur"
            ].sum()
            facts.append(f"{without / total:.1%} of third party spend has no contract on file")
    return SummarySection(
        title="Who the suppliers are",
        headline=f"{len(approved)} canonical suppliers",
        facts=facts,
    )


def _levers(run_id: str) -> SummarySection | None:
    from levers.definitions import SPEND_KINDS
    from levers.engine import has_artifact, load_artifact

    if not has_artifact(run_id):
        return None
    artifact = load_artifact(run_id)
    quantified = [l for l in artifact.levers if l.status == "quantified" and l.kind in SPEND_KINDS]
    blocked = [l for l in artifact.levers if l.status == "not_assessable"]

    facts = [
        f"{lever.name}: {lever.potential_base:,.0f} EUR on {lever.net_base:,.0f} of spend"
        for lever in quantified[:MAX_FACTS]
    ]
    if blocked:
        facts.append(f"{len(blocked)} lever(s) could not be assessed from this data")
    return SummarySection(
        title="What can be acted on",
        headline=(
            f"{artifact.total_base:,.0f} EUR identified "
            f"({artifact.total_low:,.0f} to {artifact.total_high:,.0f})"
        ),
        facts=facts,
    )


# --- the questions ---------------------------------------------------------


def _add_questions(run_id, summary, table, client, logger) -> ExecutiveSummary:
    context = analysis_context(run_id, table)
    try:
        result = run_agent(definition(), build_input(context), client=client, logger=logger)
    except Exception as error:
        # Questions are commentary; losing them must not cost the summary.
        logger.warning("sme questions unavailable: %s", error)
        return summary

    return summary.model_copy(
        update={
            "sme_questions": [
                SmeQuestionRecord(**question.model_dump()) for question in result.output.questions
            ],
            "llm_call": LlmCall(
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                duration_seconds=result.duration_seconds,
            ),
        }
    )


def analysis_context(run_id: str, table: pd.DataFrame | None = None) -> dict:
    """Everything the question and chat agents may see: aggregates, never rows."""
    if table is None:
        table = load_table(run_id) if has_table(run_id) else pd.DataFrame()

    context: dict = {"run_id": run_id}
    # The chain needs the eligibility the rule engine writes. A run that stopped
    # earlier gets a shorter context rather than an error.
    if not table.empty and "include_spend_analysis" in table.columns:
        report = spend_chain(table)
        context["spend_chain"] = [
            {"step": step.label, "eur": round(step.amount, 2), "note": step.note}
            for step in report.chain
        ]
        context["rows_analysed"] = report.rows_analysed
        context["intercompany_suppliers"] = report.intercompany_suppliers

    for name, loader in _CONTEXT_PARTS.items():
        try:
            value = loader(run_id, table)
        except Exception:
            continue
        if value:
            context[name] = value
    return context


def _quality_context(run_id, table):
    from profiling.data_profiling import has_report, load_report

    if not has_report(run_id):
        return None
    report = load_report(run_id)
    return {
        "findings": [
            {"check": f.check, "result": f.result, "severity": f.severity, "rows": f.affected_rows}
            for f in report.findings
        ],
        "category_decision": report.category_decision,
        "reconciliation": [
            {
                "company": entry.company,
                "detail": round(entry.detail_total, 2),
                "stated": round(entry.stated_total, 2),
                "difference": round(entry.difference, 2),
            }
            for entry in report.reconciliation
        ],
    }


def _lever_context(run_id, table):
    from levers.engine import has_artifact, load_artifact

    if not has_artifact(run_id):
        return None
    artifact = load_artifact(run_id)
    return {
        "addressable_spend": round(artifact.addressable_spend, 2),
        "total_potential": {
            "low": round(artifact.total_low, 2),
            "base": round(artifact.total_base, 2),
            "high": round(artifact.total_high, 2),
        },
        "levers": [
            {
                "name": lever.name,
                "status": lever.status,
                "kind": lever.kind,
                "reason": lever.status_reason,
                "spend_it_applies_to": round(lever.net_base, 2),
                "potential_base": round(lever.potential_base, 2),
                "rate_base": lever.rate_base,
                "confidence": lever.confidence,
                "missing_fields": lever.missing_fields,
                "metric": lever.metric,
            }
            for lever in artifact.levers
        ],
        "data_requests": [
            {"field": r.label, "unlocks": r.unlocks} for r in artifact.data_requests
        ],
        "companies": [
            {
                "company": entry.company,
                "spend": round(entry.spend, 2),
                "purchase_order_coverage": round(entry.po_coverage, 3),
                "share_without_contract": round(entry.uncontracted_share, 3),
            }
            for entry in artifact.benchmark
        ],
    }


def _currency_context(run_id, table):
    from fx.currency import has_report, load_report

    if not has_report(run_id):
        return None
    report = load_report(run_id)
    return {
        "rate_source": report.rate_source,
        "net_eur": round(report.spend_net_eur, 2),
        "gross_eur": round(report.spend_gross_eur, 2),
        "credit_notes_eur": round(report.credit_volume_eur, 2),
        "by_currency": [
            {"currency": e.currency, "rows": e.rows, "eur": round(e.sum_eur, 2)}
            for e in report.breakdown
        ],
    }


_CONTEXT_PARTS = {
    "data_quality": _quality_context,
    "levers": _lever_context,
    "currency": _currency_context,
}
