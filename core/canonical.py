"""The canonical procurement schema (SYSTEMCONCEPT section 8).

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
from typing import Literal

import pandas as pd

# What a field is for, which decides how its absence is treated.
#
#   core      nothing works without it -- absence is a serious finding
#   standard  usually present, carries the main analyses
#   extended  unlocks a specific lever when present; absent is normal and is
#             reported at the lever it blocks, not as a data quality problem
FieldTier = Literal["core", "standard", "extended"]


@dataclass(frozen=True)
class CanonicalField:
    key: str
    label: str
    description: str
    tier: FieldTier

    @property
    def required(self) -> bool:
        return self.tier == "core"


CANONICAL_FIELDS = (
    CanonicalField(
        "company",
        "Company",
        "Identifier of the portfolio company or legal entity that booked the "
        "transaction, such as a company code or operating unit. If the export "
        "identifies companies only by name and carries no code, map that column "
        "here instead.",
        tier="core",
    ),
    CanonicalField(
        "company_name",
        "Company Name",
        "Readable name of the portfolio company, where the export carries one in "
        "addition to a code. Leave empty if the only company column has already "
        "been mapped to company.",
        tier="standard",
    ),
    CanonicalField(
        "supplier",
        "Supplier",
        "Name of the vendor or supplier being paid. ERP systems call this vendor, "
        "supplier name, creditor or business partner.",
        tier="core",
    ),
    CanonicalField(
        "supplier_id",
        "Supplier ID",
        "Stable identifier of the supplier in the source system, such as a vendor "
        "number or partner id. Usually numeric and often zero-padded.",
        tier="standard",
    ),
    CanonicalField(
        "amount_local",
        "Local Amount",
        "Transaction amount in the currency of the document itself. If the export "
        "has several amount columns, this is the one that belongs with the local "
        "currency column.",
        tier="core",
    ),
    CanonicalField(
        "amount_group",
        "Group Amount",
        "The same amount already converted into the group reporting currency. Map "
        "this only if the export genuinely provides a second, converted amount.",
        tier="standard",
    ),
    CanonicalField(
        "currency",
        "Currency",
        "Currency of the local amount, typically a three-letter ISO code such as "
        "EUR or USD.",
        tier="core",
    ),
    CanonicalField(
        "posting_date",
        "Posting Date",
        "Date the transaction was posted to the ledger. If the export has both a "
        "posting and a document date, this is the ledger one.",
        tier="core",
    ),
    CanonicalField(
        "document_date",
        "Document Date",
        "Date printed on the invoice or source document, which can differ from the "
        "posting date.",
        tier="standard",
    ),
    CanonicalField(
        "invoice_number",
        "Invoice Number",
        "Invoice or accounting document number identifying the transaction.",
        tier="standard",
    ),
    CanonicalField(
        "purchase_order",
        "Purchase Order",
        "Purchase order or purchasing document reference, where the transaction "
        "has one. Often empty for a large share of rows.",
        tier="standard",
    ),
    CanonicalField(
        "gl_account",
        "GL Account",
        "General ledger account number the cost was booked to.",
        tier="standard",
    ),
    CanonicalField(
        "gl_description",
        "GL Description",
        "Text describing the GL account. This is accounting language such as "
        "'Consulting expenses' or 'Freight costs', not a procurement category.",
        tier="standard",
    ),
    CanonicalField(
        "cost_center",
        "Cost Center",
        "Cost center the spend was allocated to.",
        tier="standard",
    ),
    CanonicalField(
        "profit_center",
        "Profit Center",
        "Profit center the spend was allocated to.",
        tier="standard",
    ),
    CanonicalField(
        "category",
        "Procurement Category",
        "Procurement category describing what was actually bought, for example "
        "Logistics, IT Services or Facility Management. Map a column here only if "
        "it genuinely classifies the purchase. If the column is really the text of "
        "the GL account, map it to gl_description and leave this one empty.",
        tier="standard",
    ),
    # --- extended: present only in richer exports, each unlocking a lever ---
    CanonicalField(
        "item_code",
        "Item Code",
        "Article, material or service number identifying *what* was bought, as "
        "opposed to who sold it. The key that lets the same item be compared "
        "across companies. Not the document or order number.",
        tier="extended",
    ),
    CanonicalField(
        "quantity",
        "Quantity",
        "How many units the line covers. A count of goods or hours, never an "
        "amount of money and never a document number.",
        tier="extended",
    ),
    CanonicalField(
        "unit_price",
        "Unit Price",
        "Price for a single unit, where the export states it separately from the "
        "line total. Map only a genuine per-unit price, not the line amount.",
        tier="extended",
    ),
    CanonicalField(
        "unit_of_measure",
        "Unit of Measure",
        "The unit the quantity is counted in, such as PC, KG, HOUR or LITRE. "
        "Without it two quantities cannot be compared.",
        tier="extended",
    ),
    CanonicalField(
        "payment_terms",
        "Payment Terms",
        "The agreed terms of payment, such as 'NET30' or '2/10 net 30'. A term, "
        "not a date.",
        tier="extended",
    ),
    CanonicalField(
        "contract_id",
        "Contract ID",
        "Reference to the contract or framework agreement the transaction falls "
        "under. Distinct from the purchase order, which covers a single order.",
        tier="extended",
    ),
    CanonicalField(
        "contract_end_date",
        "Contract End Date",
        "When the governing contract expires. Only map a contract validity date, "
        "never the posting or document date.",
        tier="extended",
    ),
    CanonicalField(
        "delivery_location",
        "Delivery Location",
        "Where the goods or services were delivered: plant, site or ship-to "
        "address. Not the cost center and not the company.",
        tier="extended",
    ),
)

CANONICAL_KEYS = frozenset(field.key for field in CANONICAL_FIELDS)
FIELDS_BY_TIER = {
    tier: tuple(field for field in CANONICAL_FIELDS if field.tier == tier)
    for tier in ("core", "standard", "extended")
}

_BY_KEY = {field.key: field for field in CANONICAL_FIELDS}


def field_by_key(key: str) -> CanonicalField:
    return _BY_KEY[key]


def company_key(table: pd.DataFrame) -> pd.Series:
    """Which company a row belongs to, as everything downstream should ask.

    The canonical name where company normalization has run, the raw name from the
    export where it has not. A run written before that stage existed therefore
    groups exactly as it always did.
    """
    normalized = table.get("company_normalized")
    raw = table["company_name"].astype(str).str.strip()
    if normalized is None:
        return raw
    canonical = normalized.astype(str).str.strip()
    return canonical.where(canonical != "", raw)


# What makes two rows the same booking posted twice.
DUPLICATE_FIELDS = ("supplier", "amount_local", "posting_date", "invoice_number")


def company_identity(table: pd.DataFrame) -> pd.Series:
    """A company key that is unique across submissions, not just within one.

    The canonical id where company normalization has run, the export's own code
    where it has not. Two ERPs both numbering their entities from 1000 would
    otherwise make bookings of unrelated companies look identical.
    """
    canonical = table.get("company_canonical_id")
    raw = table["company"].astype(str).str.strip()
    if canonical is None:
        return raw
    canonical = canonical.astype(str).str.strip()
    return canonical.where(canonical != "", raw)


def duplicate_key(table: pd.DataFrame) -> pd.DataFrame:
    """The columns that together identify one booking, for duplicate detection."""
    frame = pd.DataFrame({"company": company_identity(table)}, index=table.index)
    for column in DUPLICATE_FIELDS:
        if column in table.columns:
            frame[column] = table[column]
    return frame


def bookings(table: pd.DataFrame) -> pd.DataFrame:
    """The rows that record a booking, without the totals an export embedded.

    Anything deriving a population from the data itself has to ask for this
    first. A grand total row carries the group's name in its company column and
    its own marker as a supplier; left in, it nominates itself as a company and
    turns up in the supplier list as a name to group.
    """
    if "flag_aggregate_row" not in table.columns:
        return table
    return table[~table["flag_aggregate_row"].fillna(False).astype(bool)]
