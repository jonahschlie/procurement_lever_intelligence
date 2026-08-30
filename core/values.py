"""Deterministic typing of the text values carried through ingestion.

Everything upstream keeps values exactly as they arrived. This is where they
become numbers and dates -- the rule engine's job per SYSTEMCONCEPT section 11,
and the first point at which interpreting a value is allowed.

Formats are decided per column, never per value. A real export makes clear why:

    amount_local "83,122.08"   amount_group "83122.08"
    amount_local "-5,313.98"   amount_group "-5313.98"

Read on its own, ``83,122.08`` looks like a continental decimal and would become
83.12 -- wrong by three orders of magnitude, and invisible in any total. Read as
a column, the values that carry both separators settle it: the rightmost one is
the decimal separator, so here the comma groups thousands.
"""

import re
from dataclasses import dataclass

import pandas as pd

# Ordered: ISO first, then day-first, then month-first. The order only decides
# ties, where every candidate parses the column equally well.
DATE_PATTERNS = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%Y%m%d",
)

_THOUSANDS_GROUP = re.compile(r"[.,]\d{3}(?!\d)")
_TRAILING_SIGN = re.compile(r"^(.*?)([+-])$")
_NOT_NUMERIC = re.compile(r"[^\d.,+-]")


@dataclass(frozen=True)
class AmountFormat:
    decimal_separator: str | None
    thousands_separator: str | None
    parsed: int
    failed: int


@dataclass(frozen=True)
class DateFormat:
    pattern: str | None
    parsed: int
    failed: int


def parse_amount_column(series: pd.Series) -> tuple[pd.Series, AmountFormat]:
    """Turn a text column into numbers, deciding the format from the whole column."""
    text = series.astype(str).str.strip()
    filled = text[text != ""]

    decimal, thousands = _detect_separators(filled)
    parsed = text.map(lambda value: _to_number(value, decimal, thousands))
    numbers = pd.to_numeric(parsed, errors="coerce")

    return numbers, AmountFormat(
        decimal_separator=decimal,
        thousands_separator=thousands,
        parsed=int(numbers.notna().sum()),
        failed=int(((text != "") & numbers.isna()).sum()),
    )


def parse_date_column(series: pd.Series) -> tuple[pd.Series, DateFormat]:
    """Turn a text column into dates, choosing the pattern that fits it best."""
    text = series.astype(str).str.strip()
    filled = text[text != ""]
    if filled.empty:
        return pd.to_datetime(pd.Series([pd.NaT] * len(series), index=series.index)), DateFormat(
            pattern=None, parsed=0, failed=0
        )

    best_pattern, best_hits = None, 0
    for pattern in DATE_PATTERNS:
        hits = int(pd.to_datetime(filled, format=pattern, errors="coerce").notna().sum())
        if hits > best_hits:
            best_pattern, best_hits = pattern, hits

    if best_pattern is None:
        dates = pd.Series(pd.NaT, index=series.index)
        return dates, DateFormat(pattern=None, parsed=0, failed=len(filled))

    dates = pd.to_datetime(
        text.where(text != ""), format=best_pattern, errors="coerce"
    )
    return dates, DateFormat(
        pattern=best_pattern,
        parsed=int(dates.notna().sum()),
        failed=int(((text != "") & dates.isna()).sum()),
    )


def spend_basis(local: pd.Series, group: pd.Series) -> pd.Series:
    """The amount to reckon spend with.

    The group figure wins where the export provides one, because it is already
    expressed in one currency; the local amount only fills the gaps. Section 13
    converts what is still missing.
    """
    return group.fillna(local)


def _detect_separators(filled: pd.Series) -> tuple[str | None, str | None]:
    """Work out which separator is decimal and which groups thousands."""
    if filled.empty:
        return None, None

    # A value carrying both settles it: the rightmost separator is the decimal one.
    both = filled[filled.str.contains(r"\.") & filled.str.contains(",")]
    if not both.empty:
        decimal = both.map(lambda value: max(value.rfind("."), value.rfind(",")))
        rightmost = [value[position] for value, position in zip(both, decimal)]
        chosen = max(set(rightmost), key=rightmost.count)
        return chosen, ("," if chosen == "." else ".")

    for separator in (",", "."):
        present = filled[filled.str.contains(re.escape(separator))]
        if present.empty:
            continue
        # A group that is not exactly three digits cannot be a thousands group.
        groups = present.str.replace(r"[^\d.,]", "", regex=True)
        if any(not _all_groups_of_three(value, separator) for value in groups):
            return separator, None
        return (".", separator) if separator == "," else (",", separator)

    return None, None


def _all_groups_of_three(value: str, separator: str) -> bool:
    parts = value.split(separator)
    return len(parts) > 1 and all(len(part) == 3 and part.isdigit() for part in parts[1:])


def _to_number(value: str, decimal: str | None, thousands: str | None) -> str:
    if not value:
        return ""

    cleaned = _NOT_NUMERIC.sub("", value)
    # SAP and other ledgers write the sign after the number.
    trailing = _TRAILING_SIGN.match(cleaned)
    if trailing:
        cleaned = f"{trailing.group(2)}{trailing.group(1)}"

    if thousands:
        cleaned = cleaned.replace(thousands, "")
    if decimal and decimal != ".":
        cleaned = cleaned.replace(decimal, ".")
    return cleaned
