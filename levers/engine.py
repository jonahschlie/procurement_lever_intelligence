"""Computing the levers: bases, potentials, priority.

Two numbers per lever, and the difference between them is the point:

**Gross** is what the lever is worth looked at on its own. **Net** is its
contribution once every euro has been assigned to exactly one lever. On real data
64.5% of spend qualifies for more than one, so adding gross figures would count
the same money two and three times over.

Assignment follows the precedence in the configuration, ordered by how specific
a lever's population is. Specificity is a property of the data; ordering by
assumed saving rate instead would maximise the total and bias it optimistic.

Saving rates are assumptions and are carried through to the UI so that every
figure appears next to the rate that produced it. The agent adds narrative and
ordering, never a number.
"""

from pathlib import Path

import pandas as pd

from agents.base import run_agent
from agents.lever_reasoning import build_input, definition
from core.config import EFFORT_COMPANIES, EFFORT_SUPPLIERS, LEVER_PRECEDENCE
from core.models import (
    CompanyBenchmark,
    LeverArtifact,
    LeverContributor,
    LeverResult,
    LlmCall,
)
from core.run import get_logger, record_step, step_path
from core.table import load_table, write_table
from levers.definitions import BY_ID, LEVERS

STEP = "levers"
ARTIFACT_NAME = "levers.json"
MAX_CONTRIBUTORS = 5

PRIMARY_COLUMN = "lever_primary"
UNASSIGNED = ""


def run_levers(run_id: str, *, client=None) -> LeverArtifact:
    logger = get_logger(run_id)
    table = load_table(run_id)
    rows = _addressable(table)

    memberships = {lever.lever_id: lever.membership(rows) for lever in LEVERS}
    primary = _assign_primary(rows, memberships)

    addressable = float(rows["amount_eur"].sum())
    results = [
        _measure(lever, rows, memberships[lever.lever_id], primary)
        for lever in LEVERS
    ]
    results.sort(key=lambda r: (-r.potential_base, _EFFORT_RANK[r.effort]))

    benchmark = _benchmark(rows)
    artifact = LeverArtifact(
        addressable_spend=addressable,
        levers=results,
        total_low=sum(r.potential_low for r in results),
        total_base=sum(r.potential_base for r in results),
        total_high=sum(r.potential_high for r in results),
        benchmark=benchmark,
    )
    artifact = _add_narrative(artifact, client, logger)

    _write_columns(run_id, table, rows, memberships, primary)
    target = step_path(run_id, STEP)
    (target / ARTIFACT_NAME).write_bytes(artifact.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [target / ARTIFACT_NAME])
    logger.info(
        "levers identified: %d, de-duplicated potential %.0f (base) on %.0f addressable",
        len(results),
        artifact.total_base,
        addressable,
    )
    return artifact


def load_artifact(run_id: str) -> LeverArtifact:
    return _load(step_path(run_id, STEP) / ARTIFACT_NAME)


def has_artifact(run_id: str) -> bool:
    return (step_path(run_id, STEP) / ARTIFACT_NAME).is_file()


def _addressable(table: pd.DataFrame) -> pd.DataFrame:
    """The population every lever works on: negotiable spend with a named supplier."""
    rows = table[table["include_addressable_spend"].astype(bool)]
    rows = rows[rows["amount_eur"].notna()]
    return rows[rows["supplier_normalized"].astype(str).str.strip() != ""]


def _assign_primary(rows: pd.DataFrame, memberships: dict[str, pd.Series]) -> pd.Series:
    """Give every euro to exactly one lever, most specific population first."""
    primary = pd.Series(UNASSIGNED, index=rows.index, dtype=object)
    for lever_id in LEVER_PRECEDENCE:
        member = memberships.get(lever_id)
        if member is None:
            continue
        primary = primary.where(primary != UNASSIGNED, other=member.map({True: lever_id, False: UNASSIGNED}))
    return primary


def _measure(lever, rows, member: pd.Series, primary: pd.Series) -> LeverResult:
    gross_rows = rows[member]
    net_rows = rows[primary == lever.lever_id]

    gross = float(gross_rows["amount_eur"].sum())
    net = float(net_rows["amount_eur"].sum())
    low, base, high = lever.rates

    suppliers = int(gross_rows["supplier_normalized"].nunique())
    companies = int(gross_rows["company_name"].nunique())
    effort, effort_reason = _effort(suppliers, companies)

    return LeverResult(
        lever_id=lever.lever_id,
        name=lever.name,
        mechanism=lever.mechanism,
        gross_base=gross,
        net_base=net,
        rows=int(len(gross_rows)),
        suppliers=suppliers,
        companies=companies,
        rate_low=low,
        rate_base=base,
        rate_high=high,
        potential_low=net * low,
        potential_base=net * base,
        potential_high=net * high,
        effort=effort,
        effort_reason=effort_reason,
        confidence=lever.confidence,
        confidence_reason=lever.confidence_reason,
        contributors=_contributors(gross_rows),
    )


_EFFORT_RANK = {"low": 0, "medium": 1, "high": 2}


def _effort(suppliers: int, companies: int) -> tuple[str, str]:
    """How much coordination the lever needs, from counts rather than judgement."""
    supplier_level = _band(suppliers, EFFORT_SUPPLIERS)
    company_level = _band(companies, EFFORT_COMPANIES)
    level = max(supplier_level, company_level, key=lambda name: _EFFORT_RANK[name])
    return level, (
        f"{suppliers} supplier(s) and {companies} company(ies) to coordinate"
    )


def _band(value: int, thresholds: tuple[int, int]) -> str:
    low, medium = thresholds
    if value <= low:
        return "low"
    return "medium" if value <= medium else "high"


def _contributors(rows: pd.DataFrame) -> list[LeverContributor]:
    if rows.empty:
        return []
    grouped = rows.groupby("supplier_normalized").agg(
        spend=("amount_eur", "sum"),
        rows=("amount_eur", "size"),
        companies=("company_name", "nunique"),
    )
    status = rows.groupby("supplier_normalized")["supplier_contract_status"].first()
    return [
        LeverContributor(
            supplier=str(name),
            spend=float(entry["spend"]),
            rows=int(entry["rows"]),
            companies=int(entry["companies"]),
            contract_status=str(status.get(name, "")),
        )
        for name, entry in grouped.nlargest(MAX_CONTRIBUTORS, "spend").iterrows()
    ]


def _benchmark(rows: pd.DataFrame) -> list[CompanyBenchmark]:
    """The companies compared, so a lever can be pointed where it bites first."""
    entries = []
    for company, group in rows.groupby("company_name"):
        spend = float(group["amount_eur"].sum())
        if not spend:
            continue
        uncontracted = float(
            group.loc[group.get("supplier_contract_status") == "no", "amount_eur"].sum()
        )
        entries.append(
            CompanyBenchmark(
                company=str(company),
                spend=spend,
                suppliers=int(group["supplier_normalized"].nunique()),
                po_coverage=float((group["purchase_order"].astype(str).str.strip() != "").mean()),
                uncontracted_share=uncontracted / spend,
            )
        )
    return sorted(entries, key=lambda entry: -entry.uncontracted_share)


def _add_narrative(artifact: LeverArtifact, client, logger) -> LeverArtifact:
    payload = [
        {
            "lever_id": lever.lever_id,
            "name": lever.name,
            "mechanism": lever.mechanism,
            "spend_it_applies_to": round(lever.net_base, 2),
            "estimated_saving_range": [
                round(lever.potential_low, 2),
                round(lever.potential_base, 2),
                round(lever.potential_high, 2),
            ],
            "suppliers": lever.suppliers,
            "companies": lever.companies,
            "effort": lever.effort,
            "confidence": lever.confidence,
            "confidence_reason": lever.confidence_reason,
            "largest_contributors": [
                {
                    "supplier": c.supplier,
                    "spend": round(c.spend, 2),
                    "companies": c.companies,
                    "contract": c.contract_status,
                }
                for c in lever.contributors
            ],
        }
        for lever in artifact.levers
    ]
    benchmark = [
        {
            "company": entry.company,
            "spend": round(entry.spend, 2),
            "suppliers": entry.suppliers,
            "purchase_order_coverage": round(entry.po_coverage, 3),
            "share_without_contract": round(entry.uncontracted_share, 3),
        }
        for entry in artifact.benchmark
    ]

    result = run_agent(definition(), build_input(payload, benchmark), client=client, logger=logger)
    narratives = {n.lever_id: n for n in result.output.levers}

    levers = [
        lever.model_copy(
            update={
                "opportunity": narratives[lever.lever_id].opportunity,
                "next_steps": narratives[lever.lever_id].next_steps,
            }
        )
        if lever.lever_id in narratives
        else lever
        for lever in artifact.levers
    ]
    return artifact.model_copy(
        update={
            "levers": levers,
            "priority_rationale": result.output.priority_rationale,
            "agent_order": [
                lever_id for lever_id in result.output.recommended_order if lever_id in BY_ID
            ],
            "agent_order_reason": result.output.order_reason,
            "llm_call": LlmCall(
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                duration_seconds=result.duration_seconds,
            ),
        }
    )


def _write_columns(run_id, table, rows, memberships, primary) -> None:
    """Membership and assignment as columns, so any figure can be filtered back."""
    for lever_id, member in memberships.items():
        column = pd.Series(False, index=table.index)
        column.loc[rows.index] = member
        table[f"lever_{lever_id}"] = column

    assignment = pd.Series(UNASSIGNED, index=table.index, dtype=object)
    assignment.loc[rows.index] = primary
    table[PRIMARY_COLUMN] = assignment
    write_table(run_id, table, STEP, note="lever membership and primary assignment")


def _load(path: Path) -> LeverArtifact:
    return LeverArtifact.model_validate_json(path.read_bytes())
