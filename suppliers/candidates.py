"""Deterministic half of supplier normalization: find who might be the same.

Without a supplier id there is no stable key, so matching works on names. This
module handles what needs no judgement: cleanup-identical names merge outright,
and a similarity score decides which pairs are worth anyone's attention. Whether
'Atlas Frght & Log.' and 'Atlas Freight & Logistics' are one firm is semantics
and belongs to the matching agent; whether they are worth asking about is
arithmetic and belongs here.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations

from core.config import SUPPLIER_AUTO_MERGE, SUPPLIER_CANDIDATE_FLOOR

LEGAL_SUFFIXES = frozenset(
    """ltd limited inc corp corporation gmbh bv nv sa sl srl sarl ab as oy kft
    plc llc kg ag se spa lda sp z o o spzoo zoo co company""".split()
)

# Connectives carry no identity: 'Studies & Reports' and 'Studies and Reports'
# are the same name. The ampersand already falls to the punctuation strip.
CONNECTIVES = frozenset({"and", "und", "the", "of"})


@dataclass(frozen=True)
class CandidatePair:
    left: str
    right: str
    similarity: float


def normalize_name(name: str) -> str:
    """Case, punctuation and legal-suffix blind form of a supplier name."""
    text = re.sub(r"[^\w\s]", " ", name.casefold())
    words = [
        word for word in text.split() if word not in LEGAL_SUFFIXES and word not in CONNECTIVES
    ]
    # Dotted legal forms fall apart under the punctuation strip: 'S.A' becomes
    # 's a'. Single letters at the tail are remnants of those, not identity.
    while words and len(words[-1]) == 1:
        words.pop()
    return " ".join(words)


def similarity(left: str, right: str) -> float:
    """Best of character-level and token-level similarity of the normal forms.

    Characters catch misspellings, tokens catch reordering and abbreviation --
    either alone misses cases the other sees.
    """
    a, b = normalize_name(left), normalize_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    chars = SequenceMatcher(None, a, b).ratio()
    left_tokens, right_tokens = set(a.split()), set(b.split())
    tokens = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    return max(chars, tokens)


def build_candidates(
    names: list[str],
) -> tuple[list[CandidatePair], list[CandidatePair], list[CandidatePair]]:
    """Score every pair once and sort it into auto-merge, grey zone, or neither."""
    auto, grey, below = [], [], []
    for left, right in combinations(sorted(set(names)), 2):
        score = similarity(left, right)
        if score < SUPPLIER_CANDIDATE_FLOOR:
            continue
        pair = CandidatePair(left, right, round(score, 3))
        if score >= SUPPLIER_AUTO_MERGE:
            auto.append(pair)
        else:
            grey.append(pair)
    return auto, grey, below
