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
from core.canonical import company_key, field_by_key
from core.config import EFFORT_COMPANIES, EFFORT_SUPPLIERS, LEVER_PRECEDENCE
from core.models import (
    CompanyBenchmark,
    DataRequest,
    LeverArtifact,
    LeverContributor,
    LeverResult,
    LlmCall,
)
from core.run import get_logger, record_step, step_path
from core.table import load_table, write_table
from levers.definitions import BY_ID, LEVERS, SPEND_KINDS

STEP = "levers"
ARTIFACT_NAME = "levers.json"
MAX_CONTRIBUTORS = 5

PRIMARY_COLUMN = "lever_primary"
UNASSIGNED = ""

_STATUS_RANK = {"quantified": 0, "not_applicable": 1, "not_assessable": 2}


def assess(lever, rows: pd.DataFrame) -> tuple[str, str, list[str]]:
    """Can this lever be measured here, and if not, what is missing.

    A zero base is ambiguous on its own: it can mean the data was checked and held
    nothing, or that it could never be checked. The two call for opposite
    responses -- accept the result, or ask for more data -- so they are separated
    before any measuring happens.
    """
    missing = _missing_fields(lever, rows)
    if missing is not None:
        labels = ", ".join(field_by_key(key).label for key in missing)
        return (
            "not_assessable",
            f"The submission carries no {labels}, which this lever is measured from.",
            missing,
        )
    if lever.unavailable_reason:
        return "not_assessable", lever.unavailable_reason, []
    if lever.membership is None:
        return "not_assessable", "No measurement is defined for this lever.", []
    return "quantified", "", []


def _missing_fields(lever, rows: pd.DataFrame) -> list[str] | None:
    """The gap in the closest requirement, or None when one is satisfied."""
    if not lever.requires:
        return None
    gaps = [sorted(option - _available(rows, option)) for option in lever.requires]
    closest = min(gaps, key=len)
    return None if not closest else closest


def _available(rows: pd.DataFrame, keys: set[str]) -> set[str]:
    """Fields that exist and actually carry something."""
    present = set()
    for key in keys:
        if key in rows.columns and (rows[key].astype(str).str.strip() != "").any():
            present.add(key)
    return present


def _data_requests(results: list[LeverResult]) -> list[DataRequest]:
    """What to ask the portfolio company for, and which levers it would unlock."""
    unlocks: dict[str, list[str]] = {}
    for result in results:
        for key in result.missing_fields:
            unlocks.setdefault(key, []).append(result.name)
    return [
        DataRequest(field=key, label=field_by_key(key).label, unlocks=sorted(levers))
        for key, levers in sorted(unlocks.items())
    ]


def run_levers(run_id: str, *, client=None) -> LeverArtifact:
    logger = get_logger(run_id)
    table = load_table(run_id)
    rows = _addressable(table)

    assessments = {lever.lever_id: assess(lever, rows) for lever in LEVERS}
    memberships = {
        lever.lever_id: lever.membership(rows)
        for lever in LEVERS
        if lever.membership is not None and assessments[lever.lever_id][0] != "not_assessable"
    }
    # Only spend levers claim euros; a risk figure is an exposure, not a potential.
    primary = _assign_primary(
        rows, {k: v for k, v in memberships.items() if BY_ID[k].kind in SPEND_KINDS}
    )

    addressable = float(rows["amount_eur"].sum())
    results = [
        _measure(lever, rows, memberships.get(lever.lever_id), primary, *assessments[lever.lever_id])
        for lever in LEVERS
    ]
    results.sort(key=lambda r: (_STATUS_RANK[r.status], -r.potential_base, _EFFORT_RANK[r.effort]))

    counted = [r for r in results if r.kind in SPEND_KINDS and r.status == "quantified"]
    artifact = LeverArtifact(
        addressable_spend=addressable,
        levers=results,
        total_low=sum(r.potential_low for r in counted),
        total_base=sum(r.potential_base for r in counted),
        total_high=sum(r.potential_high for r in counted),
        benchmark=_benchmark(rows),
        data_requests=_data_requests(results),
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


def _measure(lever, rows, member, primary, status, reason, missing) -> LeverResult:
    if member is None:
        return LeverResult(
            lever_id=lever.lever_id,
            name=lever.name,
            mechanism=lever.mechanism,
            status=status,
            status_reason=reason,
            kind=lever.kind,
            required_fields=sorted(set().union(*lever.requires)) if lever.requires else [],
            missing_fields=missing,
            gross_base=0.0,
            net_base=0.0,
            rows=0,
            suppliers=0,
            companies=0,
            rate_low=0.0,
            rate_base=0.0,
            rate_high=0.0,
            potential_low=0.0,
            potential_base=0.0,
            potential_high=0.0,
            effort="low",
            effort_reason="Not measured.",
            confidence=lever.confidence or "low",
            confidence_reason=lever.confidence_reason,
            contributors=[],
        )

    gross_rows = rows[member]
    net_rows = rows[primary == lever.lever_id]

    gross = float(gross_rows["amount_eur"].sum())
    net = float(net_rows["amount_eur"].sum())

    suppliers = int(gross_rows["supplier_normalized"].nunique())
    companies = int(company_key(gross_rows).nunique())
    effort, effort_reason = _effort(suppliers, companies)

    # A risk lever reports its exposure through gross_base and metric. It claims
    # no euros, so its net base stays zero and the invariant "net bases sum to the
    # addressable spend" holds across the whole catalogue.
    if lever.kind == "risk":
        net = 0.0
        low = base = high = 0.0
    else:
        low, base, high = lever.rates

    if gross == 0:
        status = "not_applicable"
        reason = "Measured against the data; no spend qualifies for this lever."

    return LeverResult(
        lever_id=lever.lever_id,
        name=lever.name,
        mechanism=lever.mechanism,
        status=status,
        status_reason=reason,
        kind=lever.kind,
        required_fields=sorted(set().union(*lever.requires)) if lever.requires else [],
        missing_fields=missing,
        metric=_metric(lever, gross_rows, gross, rows),
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


def _metric(lever, gross_rows: pd.DataFrame, gross: float, rows: pd.DataFrame) -> str:
    """A one-line figure for levers whose point is not a saving."""
    if lever.kind != "risk" or gross_rows.empty:
        return ""
    total = rows["amount_eur"].sum() or 1
    if lever.lever_id == "supplier_dependency":
        names = gross_rows["supplier_normalized"].nunique()
        return f"{names} supplier(s) hold {gross / total:.1%} of addressable spend"
    return (
        f"{gross:,.0f} EUR ({gross / total:.1%}) in "
        f"{gross_rows['currency'].nunique()} foreign currencies"
    )


def _contributors(rows: pd.DataFrame) -> list[LeverContributor]:
    if rows.empty:
        return []
    grouped = rows.assign(_company=company_key(rows)).groupby("supplier_normalized").agg(
        spend=("amount_eur", "sum"),
        rows=("amount_eur", "size"),
        companies=("_company", "nunique"),
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
    for company, group in rows.groupby(company_key(rows)):
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
        if lever.status == "quantified" and lever.kind in SPEND_KINDS
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
