"""Aggregations the summary charts read from.

Kept beside the charts rather than in the screens so the later export builds the
same figures from the same numbers.
"""

import pandas as pd

from core.canonical import company_key


# Eligibility, the euro amount and the canonical supplier arrive from three
# different stages. A run that stopped before one of them has nothing to draw.
REQUIRED = ("include_addressable_spend", "amount_eur", "supplier_normalized")


def addressable(table: pd.DataFrame) -> pd.DataFrame:
    """Negotiable spend with a named supplier -- the population every view uses."""
    if table.empty or not set(REQUIRED) <= set(table.columns):
        return table.iloc[0:0]
    rows = table[table["include_addressable_spend"].astype(bool)]
    rows = rows[rows["amount_eur"].notna()]
    return rows[rows["supplier_normalized"].astype(str).str.strip() != ""]


def supplier_spend(rows: pd.DataFrame) -> pd.Series:
    if rows.empty:
        return pd.Series(dtype=float)
    return rows.groupby("supplier_normalized")["amount_eur"].sum().sort_values(ascending=False)


def monthly_spend(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty or "posting_date" not in rows.columns:
        return pd.DataFrame(columns=["month", "spend"])
    months = pd.to_datetime(rows["posting_date"], errors="coerce").dt.to_period("M")
    frame = (
        rows.assign(month=months.astype(str))
        .loc[months.notna()]
        .groupby("month", as_index=False)["amount_eur"]
        .sum()
        .rename(columns={"amount_eur": "spend"})
    )
    return frame.sort_values("month")


CONTRACT_LABELS = {"no": "None on file", "yes": "On file", "unknown": "Not in the master"}


def contract_coverage(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty or "supplier_contract_status" not in rows.columns:
        return pd.DataFrame(columns=["company", "status", "spend"])
    frame = (
        rows.assign(company=company_key(rows))
        .groupby(["company", "supplier_contract_status"], as_index=False)["amount_eur"]
        .sum()
        .rename(columns={"amount_eur": "spend"})
    )
    frame["status"] = frame["supplier_contract_status"].map(CONTRACT_LABELS).fillna("Not in the master")
    return frame[["company", "status", "spend"]]


def lever_allocation(rows: pd.DataFrame, names: dict[str, str]) -> pd.DataFrame:
    """How the addressable spend divides across levers, each euro once."""
    if rows.empty or "lever_primary" not in rows.columns:
        return pd.DataFrame(columns=["lever", "spend"])
    frame = (
        rows[rows["lever_primary"].astype(str) != ""]
        .groupby("lever_primary", as_index=False)["amount_eur"]
        .sum()
        .rename(columns={"lever_primary": "lever", "amount_eur": "spend"})
        .sort_values("spend", ascending=False)
    )
    frame["lever"] = frame["lever"].map(lambda key: names.get(key, key))
    return frame
