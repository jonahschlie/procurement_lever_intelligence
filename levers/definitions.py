"""The lever catalogue: the standard set, and what each one needs to be measurable.

The catalogue is fixed -- these are the levers procurement work in private equity
turns on. What varies between submissions is which of them the data can support,
so every lever declares the canonical fields it requires. A lever the data cannot
reach is not silently dropped: it is reported as unassessable, naming the fields
that would unlock it, which doubles as the request list for the next data ask.

Requirements are alternatives, not one list. Price harmonisation works from
item code plus quantity plus amount, or from item code plus a stated unit price.
Insisting on one form would report "not assessable" for data that is present in
the other.

Membership rules reference only canonical columns, never a company or supplier
name, so they carry over to any submission.
"""

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from core.canonical import company_key, duplicate_key
from core.config import (
    LEVER_RATES,
    SUPPLIER_DEPENDENCY_THRESHOLD,
    TAIL_SPEND_THRESHOLD,
)


@dataclass(frozen=True)
class Lever:
    lever_id: str
    name: str
    mechanism: str
    confidence: str = ""
    confidence_reason: str = ""
    # Rows this lever could act on, before any euro is assigned to one lever only.
    # None for catalogue entries whose data is never available to measure.
    membership: Callable[[pd.DataFrame], pd.Series] | None = None
    # Equivalent field combinations, any one of which makes the lever measurable.
    requires: tuple[frozenset[str], ...] = ()
    # "saving" and "recovery" share the spend and enter the total; "risk" reports
    # an exposure and is deliberately kept out of it.
    kind: str = "saving"
    # Set where a field exists but its content cannot carry the lever.
    unavailable_reason: str = ""

    @property
    def rates(self) -> tuple[float, float, float]:
        return LEVER_RATES.get(self.lever_id, (0.0, 0.0, 0.0))


def _multi_company(rows: pd.DataFrame) -> pd.Series:
    """Suppliers billing more than one company: volume that could be bundled."""
    served = company_key(rows).groupby(rows["supplier_normalized"]).transform("nunique")
    return served > 1


def _no_contract(rows: pd.DataFrame) -> pd.Series:
    """Spend with a supplier the master lists without a contract on file."""
    if "supplier_contract_status" not in rows.columns:
        return pd.Series(False, index=rows.index)
    return rows["supplier_contract_status"] == "no"


def _maverick(rows: pd.DataFrame) -> pd.Series:
    """No purchase order and a supplier absent from the master.

    Both signals together, because either alone explains too much: a company may
    simply not use purchase orders, and a supplier may be missing from the master
    for administrative reasons. The combination is what looks like buying outside
    the agreed channel.
    """
    if "supplier_contract_status" not in rows.columns:
        return pd.Series(False, index=rows.index)
    no_po = rows["purchase_order"].astype(str).str.strip() == ""
    off_master = rows["supplier_contract_status"] == "unknown"
    return no_po & off_master


def _tail(rows: pd.DataFrame) -> pd.Series:
    """Suppliers each below a small share of the total: many bookings, little value."""
    total = rows["amount_eur"].sum()
    if not total:
        return pd.Series(False, index=rows.index)
    by_supplier = rows.groupby("supplier_normalized")["amount_eur"].transform("sum")
    return by_supplier < total * TAIL_SPEND_THRESHOLD


def _duplicate_excess(rows: pd.DataFrame) -> pd.Series:
    """The surplus copies of a duplicated booking, not every copy of it.

    The duplicate flag marks every row in a group, but one booking per group is
    legitimate -- it is the repeat that is recoverable. Counting all flagged rows
    would double the lever exactly.
    """
    if "flag_duplicate_transaction" not in rows.columns:
        return pd.Series(False, index=rows.index)
    flagged = rows["flag_duplicate_transaction"].fillna(False).astype(bool)
    key = duplicate_key(rows)
    if len(key.columns) < 2:
        return pd.Series(False, index=rows.index)
    # Keep every row of a group except the first: that is the excess.
    return flagged & key[flagged].duplicated(keep="first").reindex(rows.index, fill_value=False)


def _dependency(rows: pd.DataFrame) -> pd.Series:
    """Spend with suppliers each large enough that losing them would hurt."""
    total = rows["amount_eur"].sum()
    if not total:
        return pd.Series(False, index=rows.index)
    by_supplier = rows.groupby("supplier_normalized")["amount_eur"].transform("sum")
    return by_supplier > total * SUPPLIER_DEPENDENCY_THRESHOLD


def _foreign_currency(rows: pd.DataFrame) -> pd.Series:
    """Spend settled in a currency other than the reporting one."""
    if "currency" not in rows.columns:
        return pd.Series(False, index=rows.index)
    currency = rows["currency"].astype(str).str.strip()
    return (currency != "") & (currency != "EUR")


LEVERS = (
    Lever(
        lever_id="duplicate_payments",
        name="Duplicate Payments",
        mechanism=(
            "Bookings identical in company, supplier, amount, date and document "
            "number. The repeat is recoverable cash, claimed back rather than "
            "negotiated."
        ),
        membership=_duplicate_excess,
        kind="recovery",
        confidence="high",
        confidence_reason=(
            "An exact match on five fields. Only the surplus copy of each group "
            "counts, since one booking per group is legitimate."
        ),
    ),
    Lever(
        lever_id="tail_spend",
        name="Tail Spend",
        mechanism=(
            "Many small suppliers absorb transaction cost out of proportion to their "
            "value. Bundling and automating them saves process cost more than price."
        ),
        membership=_tail,
        confidence="high",
        confidence_reason="Derived from the spend distribution alone, with no external source.",
    ),
    Lever(
        lever_id="maverick",
        name="Maverick / Process Compliance",
        mechanism=(
            "Purchases made without a purchase order and outside the supplier master "
            "bypass the agreed channel. Routing them through it restores control."
        ),
        membership=_maverick,
        confidence="low",
        confidence_reason=(
            "Rests on an absence. Without a stated purchasing policy, a missing purchase "
            "order is an observation rather than a breach, so the base may overstate."
        ),
    ),
    Lever(
        lever_id="contract_coverage",
        name="Contract Coverage",
        mechanism=(
            "Spend with suppliers that have no contract on file. Putting it under "
            "agreed terms captures conditions without needing more volume."
        ),
        membership=_no_contract,
        confidence="medium",
        confidence_reason=(
            "Rests on the submitted supplier master, which covers only part of the spend. "
            "Suppliers absent from it are excluded, so the base is understated rather than "
            "inflated."
        ),
    ),
    Lever(
        lever_id="supplier_consolidation",
        name="Supplier Consolidation",
        mechanism=(
            "Several companies buy from the same supplier independently. Bundling the "
            "volume and negotiating once turns eight small buyers into one large one."
        ),
        membership=_multi_company,
        confidence="high",
        confidence_reason=(
            "Rests on confirmed supplier names and the company each row belongs to, both "
            "reviewed in this run."
        ),
    ),
    # --- risk: reported with a figure, deliberately outside the savings total ---
    Lever(
        lever_id="supplier_dependency",
        name="Supplier Dependency",
        mechanism=(
            "Spend concentrated on a few suppliers. Not a saving but an exposure: "
            "concentration is negotiating power on the supplier's side of the table."
        ),
        membership=_dependency,
        kind="risk",
        confidence="high",
        confidence_reason="Derived from the spend distribution alone.",
    ),
    Lever(
        lever_id="fx_exposure",
        name="Currency Exposure",
        mechanism=(
            "Spend settled in foreign currency. An exposure to be hedged or "
            "addressed through currency clauses, not a saving to be booked."
        ),
        membership=_foreign_currency,
        kind="risk",
        confidence="high",
        confidence_reason="Read directly from the currency of each booking.",
    ),
    # --- catalogue entries the data has to earn: no membership, only a requirement ---
    Lever(
        lever_id="price_harmonisation",
        name="Price Harmonisation",
        mechanism=(
            "The same item bought at different prices by different companies. "
            "Levelling to the best price paid is the most direct saving there is."
        ),
        requires=(
            frozenset({"item_code", "quantity", "amount_local"}),
            frozenset({"item_code", "unit_price"}),
        ),
    ),
    Lever(
        lever_id="volume_rebates",
        name="Volume Rebates and Tiering",
        mechanism=(
            "Aggregated volume per item crossing a rebate threshold that single "
            "companies never reach on their own."
        ),
        requires=(frozenset({"item_code", "quantity"}),),
    ),
    Lever(
        lever_id="demand_management",
        name="Demand Management",
        mechanism=(
            "Reducing what is bought rather than what it costs: specification, "
            "consumption and standardisation across companies."
        ),
        requires=(frozenset({"item_code", "quantity", "unit_of_measure"}),),
    ),
    Lever(
        lever_id="payment_terms",
        name="Payment Terms",
        mechanism=(
            "Harmonising terms towards the best already agreed in the group frees "
            "working capital without touching price."
        ),
        requires=(frozenset({"payment_terms"}),),
    ),
    Lever(
        lever_id="contract_renegotiation",
        name="Contract Renegotiation",
        mechanism=(
            "Contracts approaching expiry are the natural moment to renegotiate, "
            "and expired ones are running on terms nobody agreed recently."
        ),
        requires=(frozenset({"contract_id", "contract_end_date"}),),
    ),
    Lever(
        lever_id="logistics_consolidation",
        name="Logistics Consolidation",
        mechanism=(
            "Deliveries to nearby locations bundled into fewer, fuller shipments."
        ),
        requires=(frozenset({"delivery_location", "quantity"}),),
    ),
    # --- the field is there; its content cannot carry the lever ---
    Lever(
        lever_id="category_consolidation",
        name="Category Consolidation",
        mechanism=(
            "Several suppliers delivering the same category, bundled into fewer "
            "relationships with more volume each."
        ),
        requires=(frozenset({"category"}),),
        unavailable_reason=(
            "The category column duplicates the GL classification rather than "
            "describing what was bought, so it cannot group purchases by category. "
            "Generated categories would unlock this lever."
        ),
    ),
    Lever(
        lever_id="spend_growth",
        name="Spend Growth",
        mechanism=(
            "Categories or suppliers growing faster than the business, which is "
            "where creeping cost hides."
        ),
        requires=(frozenset({"posting_date"}),),
        unavailable_reason=(
            "The data covers a single period. Growth needs at least two to compare, "
            "so a second year would unlock this lever."
        ),
    ),
)

BY_ID = {lever.lever_id: lever for lever in LEVERS}
SPEND_KINDS = ("saving", "recovery")
