"""Which suppliers are actually the group buying from itself.

A portfolio company billing a sister company is not procurement spend: nothing
about it is negotiable, and counting it inflates every supplier figure. On a real
submission it was 9.6% of net spend.

Group membership is nowhere in the data as such, so it is derived from two
independent signals -- neither of which hardcodes a company name:

**A. The data names its own companies.** Every row carries the entity that booked
it. A supplier name close to one of those entities is the group buying from
itself.

**B. Entities share a stem.** Tokens appearing in most of the group's company
names are the group's name. Suppliers carrying that stem are candidates.

On the submission in hand both signals independently returned the same five
suppliers, signal A at similarity 1.00, and the stem was derived rather than
configured.

What neither signal can see is a group entity that appears *only* as a supplier
and in no company column -- a parent outside the analysed scope, say. That gap is
closed by letting the user mark a supplier as intercompany by hand.
"""

from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from core.canonical import bookings
from core.config import INTERCOMPANY_MATCH, INTERCOMPANY_STEM_SHARE
from suppliers.candidates import normalize_name, similarity


@dataclass(frozen=True)
class IntercompanyCandidate:
    supplier: str
    score: float
    matched_entity: str | None
    reasons: list[str] = field(default_factory=list)


def group_entities(table: pd.DataFrame) -> list[str]:
    """The companies this analysis is about, as named by the data itself."""
    rows = bookings(table)
    names = set()
    # The canonical name first where company normalization has run: matching
    # against one spelling per entity beats matching against all of them.
    for column in ("company_normalized", "company_name", "company"):
        if column in rows.columns:
            names |= {value.strip() for value in rows[column].astype(str) if value.strip()}
    # A company identifier that is purely numeric carries no name to match against.
    return sorted(name for name in names if not name.replace("-", "").isdigit())


def group_stem(entities: list[str]) -> set[str]:
    """Tokens shared by most entity names -- the group's own name."""
    if len(entities) < 2:
        return set()
    counts = Counter(token for entity in entities for token in set(normalize_name(entity).split()))
    threshold = max(2, round(len(entities) * INTERCOMPANY_STEM_SHARE))
    return {token for token, count in counts.items() if count >= threshold and len(token) > 2}


def detect_intercompany(
    table: pd.DataFrame, suppliers: list[str] | None = None
) -> list[IntercompanyCandidate]:
    """Suppliers that look like the group itself, with the evidence for each."""
    entities = group_entities(table)
    if suppliers is None:
        rows = bookings(table)
        suppliers = sorted(
            {value.strip() for value in rows["supplier"].astype(str) if value.strip()}
        )
    stem = group_stem(entities)

    candidates = []
    for supplier in suppliers:
        score, entity = _closest(supplier, entities)
        tokens = set(normalize_name(supplier).split())
        shared = tokens & stem

        reasons = []
        if score >= INTERCOMPANY_MATCH:
            reasons.append(f"matches the group company {entity!r} ({score:.2f})")
        if shared:
            reasons.append(f"carries the group name {', '.join(sorted(shared))!r}")
        if reasons:
            candidates.append(
                IntercompanyCandidate(
                    supplier=supplier,
                    score=round(max(score, 1.0 if shared else 0.0), 3),
                    matched_entity=entity if score >= INTERCOMPANY_MATCH else None,
                    reasons=reasons,
                )
            )
    return candidates


def _closest(supplier: str, entities: list[str]) -> tuple[float, str | None]:
    best, match = 0.0, None
    for entity in entities:
        score = similarity(supplier, entity)
        if score > best:
            best, match = score, entity
    return best, match
