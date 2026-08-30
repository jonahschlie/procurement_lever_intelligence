"""Rule engine: act on what profiling measured.

SYSTEMCONCEPT section 11. Every rule is deterministic and every one of them
flags rather than removes -- the row count of the working table is the same
before and after this stage (section 12). What changes is that each analysis can
now tell which rows it may use.

This is also where text becomes typed. Amounts and dates are parsed into value
columns beside the originals, so a reviewer can see both what the export said and
what the pipeline made of it.
"""

from datetime import datetime, timezone

import pandas as pd

from core.models import RuleEffect, RuleReport
from core.run import get_logger, record_step, step_path
from core.table import load_table, write_table
from core.values import parse_amounts_per_dataset, parse_dates_per_dataset, spend_basis
from profiling.data_profiling import category_is_supplier, load_confirmed

STEP = "rule_engine"
ARTIFACT_NAME = "rule_report.json"

VALUE_COLUMNS = (
    "amount_local_value",
    "amount_group_value",
    "posting_date_value",
    "document_date_value",
)
ELIGIBILITY_COLUMNS = (
    "include_spend_analysis",
    "include_supplier_analysis",
    "include_company_analysis",
    "include_category_analysis",
)


def run_rule_engine(run_id: str) -> RuleReport:
    logger = get_logger(run_id)
    table = load_table(run_id)
    profile = load_confirmed(run_id)

    datasets = table["dataset_id"]
    local, _ = parse_amounts_per_dataset(table["amount_local"], datasets)
    group, _ = parse_amounts_per_dataset(table["amount_group"], datasets)
    posting, _ = parse_dates_per_dataset(table["posting_date"], datasets)
    document, _ = parse_dates_per_dataset(table["document_date"], datasets)
    amount = spend_basis(local, group)

    table["amount_local_value"] = local
    table["amount_group_value"] = group
    table["posting_date_value"] = posting
    table["document_date_value"] = document

    flags = _flags(table, profile, local, group, amount, posting, document)
    for column, values in flags.items():
        table[column] = values

    eligibility = _eligibility(table, flags, amount, profile.category_analysis_enabled)
    for column, values in eligibility.items():
        table[column] = values

    write_table(run_id, table, STEP, note="quality flags and analysis eligibility")

    report = RuleReport(
        row_count=len(table),
        effects=[
            RuleEffect(
                rule=_RULE_LABELS[column],
                column=column,
                affected_rows=int(values.sum()),
                detail=_RULE_DETAILS[column],
            )
            for column, values in flags.items()
            if values.any()
        ],
        spend_before=float(amount.sum()),
        spend_after=float(amount[eligibility["include_spend_analysis"]].sum()),
        excluded_rows=int((~eligibility["include_spend_analysis"]).sum()),
        eligibility={
            column: int(values.sum()) for column, values in eligibility.items()
        },
    )

    target = step_path(run_id, STEP)
    path = target / ARTIFACT_NAME
    path.write_bytes(report.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [path])
    logger.info(
        "rule engine complete: spend %.2f -> %.2f, %d row(s) excluded from spend analysis",
        report.spend_before,
        report.spend_after,
        report.excluded_rows,
    )
    return report


def load_report(run_id: str) -> RuleReport:
    path = step_path(run_id, STEP) / ARTIFACT_NAME
    return RuleReport.model_validate_json(path.read_bytes())


def has_report(run_id: str) -> bool:
    return (step_path(run_id, STEP) / ARTIFACT_NAME).is_file()


def _flags(
    table: pd.DataFrame,
    profile,
    local: pd.Series,
    group: pd.Series,
    amount: pd.Series,
    posting: pd.Series,
    document: pd.Series,
) -> dict[str, pd.Series]:
    empty = {
        field: table[field].astype(str).str.strip() == ""
        for field in (
            "supplier",
            "company",
            "currency",
            "posting_date",
            "purchase_order",
            "invoice_number",
        )
    }

    aggregate = pd.Series(False, index=table.index)
    aggregate.iloc[
        [candidate.position for candidate in profile.aggregate_candidates if candidate.exclude]
    ] = True

    invoices = table["invoice_number"].astype(str).str.strip()
    key = ["company", "supplier", "amount_local", "posting_date", "invoice_number"]

    return {
        "flag_missing_supplier": empty["supplier"],
        "flag_missing_amount": amount.isna(),
        "flag_missing_currency": empty["currency"],
        "flag_missing_company": empty["company"],
        "flag_missing_posting_date": empty["posting_date"],
        "flag_missing_purchase_order": empty["purchase_order"],
        "flag_negative_amount": amount < 0,
        "flag_future_date": posting > pd.Timestamp(datetime.now(timezone.utc).date()),
        "flag_date_order": posting.notna() & document.notna() & (posting < document),
        "flag_duplicate_document": invoices.duplicated(keep=False) & (invoices != ""),
        "flag_duplicate_transaction": table.duplicated(subset=key, keep=False),
        "flag_aggregate_row": aggregate,
        # Text was present but could not be read as a number or a date.
        "flag_unparsable_amount": (~empty_text(table["amount_local"])) & local.isna(),
        "flag_unparsable_date": (~empty["posting_date"]) & posting.isna(),
        "flag_category_is_supplier": category_is_supplier(table),
    }


def empty_text(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip() == ""


def _eligibility(
    table: pd.DataFrame,
    flags: dict[str, pd.Series],
    amount: pd.Series,
    category_enabled: bool,
) -> dict[str, pd.Series]:
    """Decide per row which analyses may use it.

    A missing currency does not disqualify a row on its own: section 11 falls back
    to the group amount first, and only excludes the row when there is nothing to
    fall back to.
    """
    usable_currency = ~flags["flag_missing_currency"] | table["amount_group_value"].notna()
    spend = ~flags["flag_aggregate_row"] & amount.notna() & usable_currency

    return {
        "include_spend_analysis": spend,
        "include_supplier_analysis": spend & ~flags["flag_missing_supplier"],
        "include_company_analysis": spend & ~flags["flag_missing_company"],
        "include_category_analysis": (
            spend
            & (table["category"].astype(str).str.strip() != "")
            & ~flags["flag_category_is_supplier"]
            & category_enabled
        ),
    }


_RULE_LABELS = {
    "flag_missing_supplier": "Missing supplier",
    "flag_missing_amount": "Missing amount",
    "flag_missing_currency": "Missing currency",
    "flag_missing_company": "Missing company",
    "flag_missing_posting_date": "Missing posting date",
    "flag_missing_purchase_order": "Missing purchase order",
    "flag_negative_amount": "Negative amount",
    "flag_future_date": "Future posting date",
    "flag_date_order": "Posting before document date",
    "flag_duplicate_document": "Duplicate document number",
    "flag_duplicate_transaction": "Duplicate transaction",
    "flag_aggregate_row": "Embedded aggregate row",
    "flag_unparsable_amount": "Unreadable amount",
    "flag_unparsable_date": "Unreadable date",
    "flag_category_is_supplier": "Supplier name in the category column",
}

_RULE_DETAILS = {
    "flag_missing_supplier": "Excluded from supplier analyses; spend is unaffected.",
    "flag_missing_amount": "Excluded from spend analyses.",
    "flag_missing_currency": "Kept where a group amount exists, excluded otherwise.",
    "flag_missing_company": "Excluded from cross-company analyses.",
    "flag_missing_posting_date": "Flagged; time-based analyses skip the row.",
    "flag_missing_purchase_order": "Flagged; PO analyses run on covered rows only.",
    "flag_negative_amount": "Credit memo, reversal or refund. Kept.",
    "flag_future_date": "Posted after today. Flagged, never corrected.",
    "flag_date_order": "Posting precedes the document date. Flagged, never corrected.",
    "flag_duplicate_document": "Same document number on several rows.",
    "flag_duplicate_transaction": "Identical in company, supplier, amount, date and document.",
    "flag_aggregate_row": "Restates other rows. Excluded from spend analyses.",
    "flag_unparsable_amount": "Text present but not readable as a number.",
    "flag_unparsable_date": "Text present but not readable as a date.",
    "flag_category_is_supplier": "Not a category. Excluded from category analyses, value kept.",
}
