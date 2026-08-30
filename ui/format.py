"""Numbers as people read them, without changing the numbers.

Streamlit hands a float to the browser exactly as pandas holds it, so an amount
shows up as 121107614.16. Formatting is a property of the display and of nothing
else: the artifacts and the canonical table keep full precision, and every
helper here only decides how a figure appears on screen.

Money columns are rounded to whole euros and cast to a nullable integer before
they are shown. Integer plus locale formatting is what guarantees "121,107,614"
with no decimal remainder, and Int64 carries blanks that a plain int cannot.
"""

import math

import pandas as pd
import streamlit as st

BLANK = "-"

_UNITS = ((1e9, "bn"), (1e6, "m"), (1e3, "k"))


def eur(value: float | None) -> str:
    """A whole-euro figure with thousand separators."""
    if not _is_number(value):
        return BLANK
    return f"{value:,.0f}"


def eur_compact(value: float | None) -> str:
    """The same figure short enough for a metric tile: 121.1m, 3.2k."""
    if not _is_number(value):
        return BLANK
    for size, unit in _UNITS:
        if abs(value) >= size:
            return f"{value / size:,.1f}{unit}"
    return f"{value:,.0f}"


def money(label: str | None = None, **kwargs) -> st.column_config.Column:
    """Column config for a whole-euro amount. Pair it with as_money()."""
    return st.column_config.NumberColumn(label, format="localized", **kwargs)


def percent(label: str | None = None, **kwargs) -> st.column_config.Column:
    """Column config for a share already expressed as a fraction of one."""
    return st.column_config.NumberColumn(label, format="percent", **kwargs)


def rate(label: str | None = None, **kwargs) -> st.column_config.Column:
    """Column config for an exchange rate or a confidence, where decimals matter."""
    return st.column_config.NumberColumn(label, format="%.4f", **kwargs)


def as_money(frame: pd.DataFrame, *columns: str) -> pd.DataFrame:
    """Round the named columns to whole units for display, on a copy.

    The caller's frame is untouched, and these frames are built for the screen
    only -- no calculation ever reads one back.
    """
    shown = frame.copy()
    for column in columns:
        if column in shown.columns:
            shown[column] = (
                pd.to_numeric(shown[column], errors="coerce").round(0).astype("Int64")
            )
    return shown


def money_config(*columns: str) -> dict[str, st.column_config.Column]:
    """The column_config mapping for a set of money columns, keyed by their labels."""
    return {column: money() for column in columns}


def _is_number(value) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return not math.isnan(value)
