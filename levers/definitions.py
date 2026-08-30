"""The levers, as data rather than code.

Each lever is a membership rule over the addressable rows plus the assumed saving
rates. Adding one means adding an entry here; the engine needs no change.

Membership rules deliberately reference only canonical columns, never a company
or supplier name, so they carry over to any submission.
"""

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from core.config import LEVER_RATES, TAIL_SPEND_THRESHOLD


@dataclass(frozen=True)
class Lever:
    lever_id: str
    name: str
    mechanism: str
    # Rows this lever could act on, before any euro is assigned to one lever only.
    membership: Callable[[pd.DataFrame], pd.Series]
    confidence: str
    confidence_reason: str

    @property
    def rates(self) -> tuple[float, float, float]:
        return LEVER_RATES[self.lever_id]


def _multi_company(rows: pd.DataFrame) -> pd.Series:
    """Suppliers billing more than one company: volume that could be bundled."""
    served = rows.groupby("supplier_normalized")["company_name"].transform("nunique")
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


LEVERS = (
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
)

BY_ID = {lever.lever_id: lever for lever in LEVERS}
