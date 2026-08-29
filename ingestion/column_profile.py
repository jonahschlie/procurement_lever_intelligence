"""Column profiles: the input the schema mapping agent reasons over.

SYSTEMCONCEPT section 4 defines that input as column names, data types and sample
values. Types are inferred here for description only -- the frame itself stays
text, so nothing reaching the rule engine has been reinterpreted.

Sample values are real client data, so they are capped and truncated: enough
context for the model to recognise a column, no more than that.
"""

import re

import pandas as pd

from core.config import MAX_SAMPLE_LENGTH, MAX_SAMPLE_VALUES
from core.models import ColumnProfile

# Enough rows to be representative without scanning millions of values.
TYPE_SAMPLE_ROWS = 200

_BOOLEANS = frozenset({"true", "false", "yes", "no"})
_INTEGER = re.compile(r"^[+-]?\d+$")
_DECIMAL = re.compile(r"^[+-]?\d{1,3}(?:[.,]\d{3})*[.,]\d+$|^[+-]?\d+[.,]\d+$")
_DATE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$"
    r"|^\d{1,2}[./-]\d{1,2}[./-]\d{4}$"
    r"|^\d{4}\d{2}\d{2}$"
)


def build_column_profiles(frame: pd.DataFrame) -> list[ColumnProfile]:
    return [_profile(frame[column], str(column)) for column in frame.columns]


def _profile(series: pd.Series, name: str) -> ColumnProfile:
    values = series.astype(str).str.strip()
    # Frames are read with keep_default_na=False, so a missing cell is an empty string.
    filled = values[values != ""]
    total = len(values)
    return ColumnProfile(
        name=name,
        inferred_type=_infer_type(filled),
        null_ratio=round(1 - len(filled) / total, 4) if total else 1.0,
        distinct_count=int(filled.nunique()),
        sample_values=_samples(filled),
    )


def _samples(filled: pd.Series) -> list[str]:
    seen: list[str] = []
    for value in filled:
        if value not in seen:
            seen.append(value)
            if len(seen) == MAX_SAMPLE_VALUES:
                break
    return [value[:MAX_SAMPLE_LENGTH] for value in seen]


def _infer_type(filled: pd.Series) -> str:
    """Name a type only when every inspected value agrees; otherwise 'string'.

    Guessing loosely here would mislead the agent about columns that merely look
    numeric, such as zero-padded supplier ids.
    """
    if filled.empty:
        return "string"

    sample = filled.head(TYPE_SAMPLE_ROWS)
    if all(value.lower() in _BOOLEANS for value in sample):
        return "boolean"
    if all(_DATE.match(value) for value in sample):
        return "date"
    if all(_INTEGER.match(value) for value in sample):
        return "integer"
    if all(_DECIMAL.match(value) or _INTEGER.match(value) for value in sample):
        return "decimal"
    return "string"
