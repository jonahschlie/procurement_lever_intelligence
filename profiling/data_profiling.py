"""Data profiling: measure quality, change nothing.

SYSTEMCONCEPT section 10. This stage only evaluates. It reads the working table,
produces a report, and leaves the data exactly as it found it -- acting on the
findings is the rule engine's job.

No model is involved. Everything here is a counted, reproducible measurement.
"""

import re
from datetime import datetime, timezone

import pandas as pd

from core.canonical import CANONICAL_FIELDS, field_by_key
from core.config import (
    CATEGORY_DEPENDENCY_RATIO,
    MAX_FINDING_EXAMPLES,
    MISSING_HIGH_RATIO,
)
from core.models import (
    AggregateCandidate,
    CompanyReconciliation,
    Finding,
    ProfilingReport,
)
from core.run import get_logger, record_step, step_path
from core.table import load_table
from core.values import parse_amount_column, parse_date_column, spend_basis

STEP = "profiling"
ARTIFACT_NAME = "profiling_report.json"
CONFIRMED_ARTIFACT_NAME = "profiling_confirmed.json"

# Words an export uses to label a row that restates other rows. Language-dependent
# and therefore the weakest signal here, which is why candidates are confirmed by
# a person rather than excluded automatically.
TOTAL_MARKERS = (
    "TOTAL",
    "SUBTOTAL",
    "SUB-TOTAL",
    "GRAND TOTAL",
    "SUM",
    "SUMME",
    "GESAMT",
    "ZWISCHENSUMME",
)
MARKER_FIELDS = ("supplier", "company_name", "gl_description", "category", "invoice_number")

# GL text that names an account without saying anything about what was bought.
LOW_VALUE_GL = (
    "miscellaneous",
    "other expenses",
    "other costs",
    "general costs",
    "sundry",
    "sonstige",
    "diverse",
    "verschiedenes",
)

LEGAL_SUFFIXES = (
    "ltd", "limited", "inc", "corp", "corporation", "gmbh", "bv", "nv", "sa", "sl",
    "srl", "ab", "as", "oy", "kft", "sp z o o", "spzoo", "plc", "llc", "kg", "ag",
)


def run_profiling(run_id: str) -> ProfilingReport:
    logger = get_logger(run_id)
    table = load_table(run_id)

    local, local_format = parse_amount_column(table["amount_local"])
    group, group_format = parse_amount_column(table["amount_group"])
    posting, posting_format = parse_date_column(table["posting_date"])
    document, _ = parse_date_column(table["document_date"])
    amount = spend_basis(local, group)

    candidates = _aggregate_candidates(table, amount)
    aggregate_positions = {candidate.position for candidate in candidates}
    detail = table.index.isin(
        [position for position in range(len(table)) if position not in aggregate_positions]
    )

    category_enabled, category_decision = _category_decision(table)

    findings = [
        *_completeness(table),
        *_consistency(table, amount, posting, document),
        *_semantic(table),
        *_aggregate_findings(candidates, amount, detail),
        *_readiness(table, amount, detail),
    ]
    reconciliation = _reconciliation(table, amount, candidates, detail)
    findings += _reconciliation_findings(reconciliation)

    report = ProfilingReport(
        row_count=len(table),
        findings=sorted(findings, key=lambda finding: _SEVERITY_ORDER[finding.severity]),
        aggregate_candidates=candidates,
        reconciliation=reconciliation,
        category_analysis_enabled=category_enabled,
        category_decision=category_decision,
        value_formats={
            "amount_local": _describe_amount(local_format),
            "amount_group": _describe_amount(group_format),
            "posting_date": posting_format.pattern or "not recognised",
        },
    )

    target = step_path(run_id, STEP)
    path = target / ARTIFACT_NAME
    path.write_bytes(report.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [path])
    logger.info(
        "profiling complete: %d finding(s), %d aggregate candidate(s)",
        len(report.findings),
        len(candidates),
    )
    return report


def confirm_profiling(
    run_id: str, excluded: set[int] | None = None, category_enabled: bool | None = None
) -> ProfilingReport:
    """Record the user's decisions on the two judgement calls this stage surfaces."""
    report = load_report(run_id)
    if excluded is not None:
        report.aggregate_candidates = [
            candidate.model_copy(update={"exclude": candidate.position in excluded})
            for candidate in report.aggregate_candidates
        ]
    if category_enabled is not None and category_enabled != report.category_analysis_enabled:
        report.category_analysis_enabled = category_enabled
        report.category_decision = f"Set by the user. {report.category_decision}"

    target = step_path(run_id, STEP)
    path = target / CONFIRMED_ARTIFACT_NAME
    path.write_bytes(report.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [target / ARTIFACT_NAME, path])

    confirmed = sum(candidate.exclude for candidate in report.aggregate_candidates)
    get_logger(run_id).info(
        "profiling confirmed: %d aggregate row(s) to exclude, category analysis %s",
        confirmed,
        "enabled" if report.category_analysis_enabled else "disabled",
    )
    return report


def load_report(run_id: str) -> ProfilingReport:
    return _load(step_path(run_id, STEP) / ARTIFACT_NAME)


def load_confirmed(run_id: str) -> ProfilingReport:
    return _load(step_path(run_id, STEP) / CONFIRMED_ARTIFACT_NAME)


def has_report(run_id: str) -> bool:
    return (step_path(run_id, STEP) / ARTIFACT_NAME).is_file()


def has_confirmed(run_id: str) -> bool:
    return (step_path(run_id, STEP) / CONFIRMED_ARTIFACT_NAME).is_file()


# --- checks -----------------------------------------------------------------

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _completeness(table: pd.DataFrame) -> list[Finding]:
    findings = []
    for field in CANONICAL_FIELDS:
        values = table[field.key].astype(str).str.strip()
        missing = int((values == "").sum())
        if not missing:
            continue
        ratio = missing / len(table)
        findings.append(
            Finding(
                check=f"Missing {field.label}",
                category="completeness",
                severity=_missing_severity(field.required, ratio),
                result=f"{ratio:.1%} ({missing:,} rows)",
                affected_rows=missing,
                detail=(
                    f"{field.label} is empty in {missing:,} of {len(table):,} rows."
                    + ("" if field.required else " The field is optional.")
                ),
                examples=_examples(table, values == ""),
            )
        )
    return findings


def _missing_severity(required: bool, ratio: float) -> str:
    if required:
        return "high" if ratio >= MISSING_HIGH_RATIO else "medium"
    return "medium" if ratio == 1.0 else "low"


def _consistency(
    table: pd.DataFrame, amount: pd.Series, posting: pd.Series, document: pd.Series
) -> list[Finding]:
    findings = []

    invoices = table["invoice_number"].astype(str).str.strip()
    duplicated = invoices.duplicated(keep=False) & (invoices != "")
    if duplicated.any():
        findings.append(
            Finding(
                check="Duplicate document numbers",
                category="consistency",
                severity="medium",
                result=f"{int(duplicated.sum()):,} rows",
                affected_rows=int(duplicated.sum()),
                detail=(
                    f"{invoices[duplicated].nunique():,} document numbers appear more than "
                    "once. Legitimate for multi-line documents, a duplicate posting otherwise."
                ),
                examples=_examples(table, duplicated),
            )
        )

    key = ["company", "supplier", "amount_local", "posting_date", "invoice_number"]
    exact = table.duplicated(subset=key, keep=False)
    if exact.any():
        findings.append(
            Finding(
                check="Duplicate transactions",
                category="consistency",
                severity="high",
                result=f"{int(exact.sum()):,} rows",
                affected_rows=int(exact.sum()),
                detail="Rows identical in company, supplier, amount, date and document number.",
                examples=_examples(table, exact),
            )
        )

    negative = amount < 0
    if negative.any():
        findings.append(
            Finding(
                check="Negative amounts",
                category="consistency",
                severity="info",
                result=f"{negative.sum() / len(table):.1%} ({int(negative.sum()):,} rows)",
                affected_rows=int(negative.sum()),
                detail="Credit memos, reversals or refunds. Flagged, kept in the dataset.",
                examples=_examples(table, negative),
            )
        )

    future = posting > pd.Timestamp(datetime.now(timezone.utc).date())
    if future.any():
        findings.append(
            Finding(
                check="Future posting dates",
                category="consistency",
                severity="high",
                result=f"{int(future.sum()):,} rows",
                affected_rows=int(future.sum()),
                detail="Posted after today, which points at a data entry or export error.",
                examples=_examples(table, future),
            )
        )

    out_of_order = posting.notna() & document.notna() & (posting < document)
    if out_of_order.any():
        findings.append(
            Finding(
                check="Posting date before document date",
                category="consistency",
                severity="medium",
                result=f"{int(out_of_order.sum()):,} rows",
                affected_rows=int(out_of_order.sum()),
                detail="A transaction posted before the document it refers to was issued.",
                examples=_examples(table, out_of_order),
            )
        )

    currencies = sorted(set(table["currency"].astype(str).str.strip()) - {""})
    findings.append(
        Finding(
            check="Currency complexity",
            category="consistency",
            severity="info" if len(currencies) <= 1 else "low",
            result=f"{len(currencies)} currencies",
            affected_rows=len(table),
            detail=(
                f"Currencies present: {', '.join(currencies) or 'none'}."
                + (" Conversion is required before spend can be compared." if len(currencies) > 1 else "")
            ),
        )
    )
    return findings


def _semantic(table: pd.DataFrame) -> list[Finding]:
    findings = []
    category = table["category"].astype(str).str.strip()
    gl = table["gl_description"].astype(str).str.strip()

    contaminated = category_is_supplier(table)
    if contaminated.any():
        findings.append(
            Finding(
                check="Supplier names in the category column",
                category="semantic",
                severity="medium",
                result=f"{category[contaminated].nunique():,} values, {int(contaminated.sum()):,} rows",
                affected_rows=int(contaminated.sum()),
                detail=(
                    "The category holds a supplier name rather than a category. These rows "
                    "inflate the apparent number of categories and are excluded from category "
                    "analysis, but the value itself is kept."
                ),
                examples=_examples(table, contaminated),
            )
        )

    both = (category != "") & (gl != "") & ~contaminated
    if both.any():
        dependency = _category_dependency(category[both], gl[both])
        identical = int((category[both].str.casefold() == gl[both].str.casefold()).sum())
        findings.append(
            Finding(
                check="Category duplicates the GL classification",
                category="semantic",
                severity="high" if dependency >= CATEGORY_DEPENDENCY_RATIO else "info",
                result=f"{dependency:.1%} determined by the GL description",
                affected_rows=int(both.sum()),
                detail=(
                    f"{category[both].nunique():,} categories against {gl[both].nunique():,} GL "
                    f"descriptions, and knowing the GL description predicts the category in "
                    f"{dependency:.1%} of rows. Comparing the strings alone would only have "
                    f"matched {identical:,} of them, because a renaming such as "
                    "'ESS - SUBCONTRACTS' to 'Subcontracts' is invisible to a string comparison."
                ),
            )
        )

    low_value = gl.str.casefold().apply(lambda text: any(term in text for term in LOW_VALUE_GL))
    if low_value.any():
        findings.append(
            Finding(
                check="Low-value GL descriptions",
                category="semantic",
                severity="low",
                result=f"{int(low_value.sum()):,} rows",
                affected_rows=int(low_value.sum()),
                detail="Descriptions such as 'Miscellaneous' or 'Other expenses' carry no "
                "procurement meaning and weaken category inference.",
                examples=_examples(table, low_value),
            )
        )

    supplier = table["supplier"].astype(str).str.strip()
    named = supplier[supplier != ""]
    if not named.empty:
        collapsed = named.map(_normalize_supplier).nunique()
        distinct = named.nunique()
        findings.append(
            Finding(
                check="Supplier name variants",
                category="semantic",
                severity="medium" if collapsed < distinct else "info",
                result=f"{distinct:,} names collapse to {collapsed:,}",
                affected_rows=int(len(named)),
                detail=(
                    f"{distinct - collapsed:,} names differ only in case, spacing or legal "
                    "suffix. Resolving them is supplier normalization, not profiling."
                ),
            )
        )
    return findings


def _aggregate_candidates(table: pd.DataFrame, amount: pd.Series) -> list[AggregateCandidate]:
    """Rows that restate other rows rather than recording a booking.

    Two independent signals, either of which makes a row a candidate: a total
    marker in a text field, or an amount sitting in a row that carries none of
    the identifiers a real booking has.
    """
    has_amount = amount.notna()

    markers = pd.Series(False, index=table.index)
    marker_hits: dict[int, str] = {}
    for column in MARKER_FIELDS:
        values = table[column].astype(str).str.upper()
        hit = values.apply(lambda text: any(marker in text for marker in TOTAL_MARKERS))
        for position in table.index[hit & ~markers]:
            marker_hits[position] = column
        markers |= hit

    identifiers = [
        table[column].astype(str).str.strip() == ""
        for column in ("posting_date", "invoice_number", "gl_account")
    ]
    structurally_empty = identifiers[0] & identifiers[1] & identifiers[2]

    candidates = []
    for position in table.index[has_amount & (markers | structurally_empty)]:
        reasons = []
        if markers[position]:
            reasons.append(f"total marker in {marker_hits.get(position, 'a text field')}")
        if structurally_empty[position]:
            reasons.append("no posting date, document number or GL account")
        row = table.loc[position]
        candidates.append(
            AggregateCandidate(
                position=int(position),
                source_row=str(row["source_row"]),
                company=str(row["company"]) or str(row["company_name"]),
                label=_candidate_label(row),
                amount=str(row["amount_local"]) or str(row["amount_group"]),
                reasons=reasons,
            )
        )
    return candidates


def _candidate_label(row: pd.Series) -> str:
    for column in MARKER_FIELDS:
        value = str(row[column]).strip()
        if value and any(marker in value.upper() for marker in TOTAL_MARKERS):
            return value
    return str(row["supplier"]).strip() or "(no label)"


def _aggregate_findings(
    candidates: list[AggregateCandidate], amount: pd.Series, detail: pd.Series
) -> list[Finding]:
    if not candidates:
        return []
    positions = [candidate.position for candidate in candidates]
    overstated = amount.sum()
    real = amount[detail].sum()
    factor = overstated / real if real else float("nan")
    return [
        Finding(
            check="Embedded aggregate rows",
            category="aggregates",
            severity="high",
            result=f"{len(candidates)} rows",
            affected_rows=len(candidates),
            detail=(
                f"Subtotal or total rows sit among the detail. Summing the amount column "
                f"without excluding them overstates spend by {factor:.2f}x "
                f"({overstated:,.2f} against {real:,.2f})."
            ),
            examples=[candidate.source_row for candidate in candidates[:MAX_FINDING_EXAMPLES]],
        )
    ]


def _reconciliation(
    table: pd.DataFrame,
    amount: pd.Series,
    candidates: list[AggregateCandidate],
    detail: pd.Series,
) -> list[CompanyReconciliation]:
    """Compare detail per company against the subtotal the export states for it."""
    stated = {
        candidate.company: amount.iloc[candidate.position]
        for candidate in candidates
        if candidate.company
    }
    if not stated:
        return []

    companies = table["company"].astype(str)
    results = []
    for company, total in sorted(stated.items()):
        rows = detail & (companies == company)
        if not rows.any():
            continue
        detail_total = float(amount[rows].sum())
        results.append(
            CompanyReconciliation(
                company=company,
                detail_total=detail_total,
                stated_total=float(total),
                difference=detail_total - float(total),
                detail_rows=int(rows.sum()),
            )
        )
    return results


def _reconciliation_findings(entries: list[CompanyReconciliation]) -> list[Finding]:
    mismatched = [entry for entry in entries if abs(entry.difference) > 0.01]
    if not mismatched:
        return []
    gap = sum(entry.difference for entry in mismatched)
    stated = sum(entry.stated_total for entry in mismatched)
    return [
        Finding(
            check="Detail does not match stated subtotals",
            category="reconciliation",
            severity="high",
            result=f"{gap:,.2f} ({gap / stated:.1%})" if stated else f"{gap:,.2f}",
            affected_rows=sum(entry.detail_rows for entry in mismatched),
            detail=(
                f"{len(mismatched)} of {len(entries)} companies disagree with the subtotal the "
                "export states for them. A systematic difference points at rows the export's "
                "own totals leave out."
            ),
            examples=[entry.company for entry in mismatched[:MAX_FINDING_EXAMPLES]],
        )
    ]


def _readiness(table: pd.DataFrame, amount: pd.Series, detail: pd.Series) -> list[Finding]:
    findings = []
    rows = table[detail]
    spend = amount[detail]

    supplier = rows["supplier"].astype(str).str.strip()
    company = rows["company"].astype(str).str.strip()
    shared = (
        pd.DataFrame({"supplier": supplier, "company": company})
        .query("supplier != '' and company != ''")
        .groupby("supplier")["company"]
        .nunique()
    )
    overlapping = int((shared > 1).sum())
    findings.append(
        Finding(
            check="Supplier overlap across companies",
            category="readiness",
            severity="info",
            result=f"{overlapping:,} of {len(shared):,} suppliers",
            affected_rows=overlapping,
            detail="Suppliers billing more than one company are the starting point for "
            "supplier consolidation.",
        )
    )

    by_supplier = spend.groupby(supplier).sum().drop(labels="", errors="ignore")
    if not by_supplier.empty and by_supplier.sum():
        top_share = by_supplier.nlargest(10).sum() / by_supplier.sum()
        findings.append(
            Finding(
                check="Spend concentration",
                category="readiness",
                severity="info",
                result=f"top 10 suppliers hold {top_share:.1%}",
                affected_rows=int(len(by_supplier)),
                detail=f"{len(by_supplier):,} suppliers carry the spend.",
            )
        )

    po = rows["purchase_order"].astype(str).str.strip() != ""
    findings.append(
        Finding(
            check="Purchase order coverage",
            category="readiness",
            severity="low" if po.mean() < 0.5 else "info",
            result=f"{po.mean():.1%}",
            affected_rows=int((~po).sum()),
            detail="PO-based analyses only run on rows that have one.",
        )
    )

    if (table["supplier_id"].astype(str).str.strip() == "").all():
        findings.append(
            Finding(
                check="No supplier identifier",
                category="readiness",
                severity="medium",
                result="absent",
                affected_rows=len(table),
                detail="Without a supplier id there is no stable key, so supplier "
                "normalization has to work on names alone.",
            )
        )
    return findings


def _category_decision(table: pd.DataFrame) -> tuple[bool, str]:
    """Does the category say anything the GL description does not already say?

    Measured as dependency rather than string similarity. A category column is
    frequently the accounting text under tidier names -- 'ESS - SUBCONTRACTS'
    becomes 'Subcontracts', 'PERSONNEL COSTS' becomes 'Payroll' -- which no
    string comparison catches, in any language. If knowing the GL description
    tells you the category, the column carries no procurement information.
    """
    category = table["category"].astype(str).str.strip()
    gl = table["gl_description"].astype(str).str.strip()

    if (category == "").all():
        return False, "No procurement category is mapped, so there is nothing to analyse."

    # Supplier names that leaked into the column are not categories, and counting
    # them would understate how strongly the real ones follow the GL description.
    both = (category != "") & (gl != "") & ~category_is_supplier(table)
    if not both.any():
        return False, "No row carries both a category and a GL description to compare."

    dependency = _category_dependency(category[both], gl[both])
    if dependency >= CATEGORY_DEPENDENCY_RATIO:
        return False, (
            f"The GL description predicts the category in {dependency:.1%} of rows "
            f"({category[both].nunique():,} categories against {gl[both].nunique():,} GL "
            "descriptions), so the column renames the accounting classification rather than "
            "adding a procurement one."
        )
    return True, (
        f"Category is populated in {(category != '').mean():.1%} of rows and is only "
        f"{dependency:.1%} predictable from the GL description, so it carries its own meaning."
    )


def _category_dependency(category: pd.Series, gl: pd.Series) -> float:
    """Share of rows whose category is the dominant one for their GL description."""
    counts = pd.DataFrame({"category": category, "gl": gl}).groupby(["gl", "category"]).size()
    return float(counts.groupby(level="gl").max().sum() / len(category))


def category_is_supplier(table: pd.DataFrame) -> pd.Series:
    """Rows whose category is actually one of the supplier names in this dataset."""
    category = table["category"].astype(str).map(_collapse)
    suppliers = set(table["supplier"].astype(str).map(_collapse)) - {""}
    return (category != "") & category.isin(suppliers)


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _normalize_supplier(name: str) -> str:
    text = re.sub(r"[^\w\s]", " ", name.casefold())
    words = [word for word in text.split() if word not in LEGAL_SUFFIXES]
    return " ".join(words)


def _describe_amount(amount_format) -> str:
    if amount_format.decimal_separator is None:
        return "integers, no separator"
    thousands = amount_format.thousands_separator
    return (
        f"decimal '{amount_format.decimal_separator}'"
        + (f", thousands '{thousands}'" if thousands else "")
        + (f", {amount_format.failed} unparsable" if amount_format.failed else "")
    )


def _examples(table: pd.DataFrame, mask: pd.Series) -> list[str]:
    return [str(value) for value in table.loc[mask, "source_row"].head(MAX_FINDING_EXAMPLES)]


def _load(path) -> ProfilingReport:
    return ProfilingReport.model_validate_json(path.read_bytes())
