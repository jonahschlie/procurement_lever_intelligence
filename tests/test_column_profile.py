import pandas as pd

from ingestion.column_profile import build_column_profiles
from ingestion.readers import read_tabular


def _profiles(**columns):
    return {p.name: p for p in build_column_profiles(pd.DataFrame(columns, dtype=str))}


def test_types_are_named_only_when_every_value_agrees():
    profiles = _profiles(
        ids=["1", "2", "3"],
        padded=["0001", "0002", "0003"],
        amounts=["1.250,00", "218,90", "-450,00"],
        dates=["2025-01-15", "2025-02-01", "2025-03-30"],
        german_dates=["15.01.2025", "01.02.2025", "30.03.2025"],
        flags=["yes", "no", "YES"],
        mixed=["1", "2", "not a number"],
        text=["Frachtkosten", "Beratung", "Bürobedarf"],
    )

    assert profiles["ids"].inferred_type == "integer"
    assert profiles["padded"].inferred_type == "integer"
    assert profiles["amounts"].inferred_type == "decimal"
    assert profiles["dates"].inferred_type == "date"
    assert profiles["german_dates"].inferred_type == "date"
    assert profiles["flags"].inferred_type == "boolean"
    # One stray value is enough to fall back -- guessing loosely would mislead the agent.
    assert profiles["mixed"].inferred_type == "string"
    assert profiles["text"].inferred_type == "string"


def test_empty_cells_count_towards_the_null_ratio():
    profiles = _profiles(partly=["a", "", "b", ""], full=["a", "b", "c", "d"])

    assert profiles["partly"].null_ratio == 0.5
    assert profiles["partly"].distinct_count == 2
    assert profiles["full"].null_ratio == 0.0


def test_only_empty_column_is_string_with_no_samples():
    profile = _profiles(blank=["", "", ""])["blank"]

    assert profile.inferred_type == "string"
    assert profile.null_ratio == 1.0
    assert profile.sample_values == []


def test_samples_are_deduplicated_capped_and_truncated():
    profile = _profiles(
        values=["a", "a", "b", "c", "d", "e", "f", "g", "x" * 200]
    )["values"]

    assert profile.sample_values == ["a", "b", "c", "d", "e"]
    assert profile.distinct_count == 8

    long_only = _profiles(values=["y" * 200])["values"]
    assert len(long_only.sample_values[0]) == 60


def test_profiles_a_real_export(sap_csv):
    frame, _ = read_tabular(sap_csv, "sap_export.csv")
    profiles = {p.name: p for p in build_column_profiles(frame)}

    assert list(profiles) == list(frame.columns)
    # Zero padding survives into the samples even though the type reads as integer.
    assert profiles["Vendor ID"].sample_values[0] == "0000123456"
    assert profiles["Amount LC"].inferred_type == "decimal"
    assert profiles["Posting Date"].inferred_type == "date"
    assert profiles["Currency"].distinct_count == 1
