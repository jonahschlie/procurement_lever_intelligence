"""The canonical procurement schema (SYSTEMCONCEPT section 5).

Every ERP export is translated into these fields; from there on the pipeline is
ERP-independent. This module is the single source of truth -- the field list
feeds the schema mapping instructions, the deterministic reconciliation of what
the agent returns, and the mapping table in the UI.

The descriptions are written to be read by the model. They spell out the
distinctions that are easy to get wrong: local versus group amount, posting
versus document date, and a procurement category versus an accounting
description.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalField:
    key: str
    label: str
    description: str
    required: bool


CANONICAL_FIELDS = (
    CanonicalField(
        "company",
        "Company",
        "The portfolio company or legal entity that booked the transaction. "
        "Often a company code, entity name or operating unit.",
        required=True,
    ),
    CanonicalField(
        "supplier",
        "Supplier",
        "Name of the vendor or supplier being paid. ERP systems call this vendor, "
        "supplier name, creditor or business partner.",
        required=True,
    ),
    CanonicalField(
        "supplier_id",
        "Supplier ID",
        "Stable identifier of the supplier in the source system, such as a vendor "
        "number or partner id. Usually numeric and often zero-padded.",
        required=False,
    ),
    CanonicalField(
        "amount_local",
        "Local Amount",
        "Transaction amount in the currency of the document itself. If the export "
        "has several amount columns, this is the one that belongs with the local "
        "currency column.",
        required=True,
    ),
    CanonicalField(
        "amount_group",
        "Group Amount",
        "The same amount already converted into the group reporting currency. Map "
        "this only if the export genuinely provides a second, converted amount.",
        required=False,
    ),
    CanonicalField(
        "currency",
        "Currency",
        "Currency of the local amount, typically a three-letter ISO code such as "
        "EUR or USD.",
        required=True,
    ),
    CanonicalField(
        "posting_date",
        "Posting Date",
        "Date the transaction was posted to the ledger. If the export has both a "
        "posting and a document date, this is the ledger one.",
        required=True,
    ),
    CanonicalField(
        "document_date",
        "Document Date",
        "Date printed on the invoice or source document, which can differ from the "
        "posting date.",
        required=False,
    ),
    CanonicalField(
        "invoice_number",
        "Invoice Number",
        "Invoice or accounting document number identifying the transaction.",
        required=False,
    ),
    CanonicalField(
        "purchase_order",
        "Purchase Order",
        "Purchase order or purchasing document reference, where the transaction "
        "has one. Often empty for a large share of rows.",
        required=False,
    ),
    CanonicalField(
        "gl_account",
        "GL Account",
        "General ledger account number the cost was booked to.",
        required=False,
    ),
    CanonicalField(
        "gl_description",
        "GL Description",
        "Text describing the GL account. This is accounting language such as "
        "'Consulting expenses' or 'Freight costs', not a procurement category.",
        required=False,
    ),
    CanonicalField(
        "cost_center",
        "Cost Center",
        "Cost center the spend was allocated to.",
        required=False,
    ),
    CanonicalField(
        "profit_center",
        "Profit Center",
        "Profit center the spend was allocated to.",
        required=False,
    ),
    CanonicalField(
        "category",
        "Procurement Category",
        "Procurement category describing what was actually bought, for example "
        "Logistics, IT Services or Facility Management. Map a column here only if "
        "it genuinely classifies the purchase. If the column is really the text of "
        "the GL account, map it to gl_description and leave this one empty.",
        required=False,
    ),
)

CANONICAL_KEYS = frozenset(field.key for field in CANONICAL_FIELDS)

_BY_KEY = {field.key: field for field in CANONICAL_FIELDS}


def field_by_key(key: str) -> CanonicalField:
    return _BY_KEY[key]
