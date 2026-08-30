"""The spend chain: from what was booked to what procurement can act on.

One function, so the report and every later view quote the same numbers. Each
step names its own population rather than subtracting loosely, because the
difference between "not addressable" and "not in the analysis at all" is exactly
what a reader will ask about.
"""

import pandas as pd

from core.models import SpendChainStep, SpendReport
from core.table import load_table


def build_spend_report(run_id: str) -> SpendReport:
    table = load_table(run_id)
    return spend_chain(table)


def spend_chain(table: pd.DataFrame) -> SpendReport:
    analysed = table["include_spend_analysis"].astype(bool)
    amount = table["amount_eur"] if "amount_eur" in table else table["amount_local_value"]
    eur = amount.where(analysed)

    gross = float(eur[eur > 0].sum())
    credits = float(-eur[eur < 0].sum())
    net = float(eur.sum())

    intercompany = _flag(table, "flag_intercompany") & analysed
    intercompany_spend = float(eur[intercompany].sum())
    third_party = net - intercompany_spend

    non_addressable = _flag(table, "flag_non_addressable") & analysed & ~intercompany
    non_addressable_spend = float(eur[non_addressable].sum())
    addressable = third_party - non_addressable_spend

    # Where the report ends is where the levers begin, so the chain runs one step
    # further. A booking with no supplier is negotiable in principle but cannot be
    # consolidated, matched to a contract or placed in a tail -- every lever works
    # on a named counterparty. Without this step the two screens quote different
    # figures under the same word.
    unnamed = analysed & ~intercompany & ~non_addressable & ~_named(table)
    unnamed_spend = float(eur[unnamed].sum())
    analysable = addressable - unnamed_spend

    chain = [
        SpendChainStep(label="Gross spend", amount=gross, note="positive bookings only"),
        SpendChainStep(
            label="Credit notes", amount=credits, delta=-credits, note="reversals and refunds"
        ),
        SpendChainStep(label="Net spend", amount=net, note="what actually flowed"),
        SpendChainStep(
            label="Intercompany",
            amount=intercompany_spend,
            delta=-intercompany_spend,
            note="the group buying from itself",
        ),
        SpendChainStep(label="Third party spend", amount=third_party),
        SpendChainStep(
            label="Not addressable",
            amount=non_addressable_spend,
            delta=-non_addressable_spend,
            note="payroll, tax, financing, provisions",
        ),
        SpendChainStep(
            label="Addressable spend",
            amount=addressable,
            note="what procurement can negotiate",
        ),
        SpendChainStep(
            label="No supplier name",
            amount=unnamed_spend,
            delta=-unnamed_spend,
            note="negotiable, but no counterparty to act on",
        ),
        SpendChainStep(
            label="Analysable spend",
            amount=analysable,
            note="what the levers are measured against",
        ),
    ]

    suppliers = sorted(
        {
            name
            for name in table.loc[intercompany, "supplier_normalized"].astype(str)
            if name.strip()
        }
    ) if "supplier_normalized" in table else []

    return SpendReport(
        rows_total=len(table),
        rows_analysed=int(analysed.sum()),
        chain=chain,
        intercompany_rows=int(intercompany.sum()),
        intercompany_suppliers=suppliers,
    )


def _named(table: pd.DataFrame) -> pd.Series:
    """Rows carrying a supplier, canonical where normalization has run."""
    for column in ("supplier_normalized", "supplier"):
        if column in table.columns:
            return table[column].astype(str).str.strip() != ""
    return pd.Series(True, index=table.index)


def _flag(table: pd.DataFrame, column: str) -> pd.Series:
    if column not in table.columns:
        return pd.Series(False, index=table.index)
    return table[column].fillna(False).astype(bool)
