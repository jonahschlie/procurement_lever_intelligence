import pandas as pd
import pytest

from core.values import parse_amount_column, parse_date_column, spend_basis


def _amounts(values):
    parsed, fmt = parse_amount_column(pd.Series(values, dtype=str))
    return list(parsed), fmt


def test_continental_format_is_read_correctly():
    parsed, fmt = _amounts(["1.250,00", "218,90", "-450,00", "12.900,00"])

    assert parsed == [1250.0, 218.9, -450.0, 12900.0]
    assert (fmt.decimal_separator, fmt.thousands_separator) == (",", ".")


def test_comma_as_thousands_separator_is_not_mistaken_for_a_decimal():
    # The case that actually occurs: most values plain, a few with a grouping comma.
    # Reading "83,122.08" on its own would give 83.12 and hide a three-order error.
    parsed, fmt = _amounts(["12485.57", "83,122.08", "-5,313.98", "17367.26"])

    assert parsed == [12485.57, 83122.08, -5313.98, 17367.26]
    assert (fmt.decimal_separator, fmt.thousands_separator) == (".", ",")


def test_the_column_decides_the_format_not_the_single_value():
    # "1,250" alone is ambiguous. Alongside a value carrying both separators it is not.
    parsed, _ = _amounts(["1,250", "9,999.50"])
    assert parsed == [1250.0, 9999.50]

    # With only commas and a two-digit group, the comma has to be the decimal mark.
    parsed, _ = _amounts(["1,250", "18,9"])
    assert parsed == [1.25, 18.9]


def test_plain_integers_need_no_separator():
    parsed, fmt = _amounts(["1250", "-450", "0"])

    assert parsed == [1250, -450, 0]
    assert fmt.decimal_separator is None


def test_trailing_sign_is_understood():
    parsed, _ = _amounts(["1234.56-", "99.00"])

    assert parsed == [-1234.56, 99.0]


def test_empty_and_unreadable_values_are_reported_not_guessed():
    parsed, fmt = _amounts(["100.00", "", "not a number"])

    assert parsed[0] == 100.0
    assert pd.isna(parsed[1]) and pd.isna(parsed[2])
    assert (fmt.parsed, fmt.failed) == (1, 1)


def test_currency_symbols_and_spaces_do_not_break_parsing():
    parsed, _ = _amounts(["EUR 1 250,00", "2.500,00"])

    assert parsed == [1250.0, 2500.0]


@pytest.mark.parametrize(
    "values,pattern,first",
    [
        (["2024-01-15", "2024-12-28"], "%Y-%m-%d", "2024-01-15"),
        (["15.01.2024", "28.12.2024"], "%d.%m.%Y", "2024-01-15"),
        (["20240115", "20241228"], "%Y%m%d", "2024-01-15"),
    ],
)
def test_date_formats_are_detected_per_column(values, pattern, first):
    parsed, fmt = parse_date_column(pd.Series(values, dtype=str))

    assert fmt.pattern == pattern
    assert str(parsed.iloc[0].date()) == first
    assert fmt.failed == 0


def test_ambiguous_day_and_month_is_resolved_by_the_whole_column():
    # 13 cannot be a month, so the column must be day-first.
    parsed, fmt = parse_date_column(pd.Series(["03/04/2024", "13/04/2024"], dtype=str))

    assert fmt.pattern == "%d/%m/%Y"
    assert str(parsed.iloc[0].date()) == "2024-04-03"


def test_empty_date_column_is_not_an_error():
    parsed, fmt = parse_date_column(pd.Series(["", ""], dtype=str))

    assert fmt.pattern is None
    assert parsed.isna().all()


def test_spend_basis_prefers_the_group_amount():
    local = pd.Series([10.0, 20.0, None])
    group = pd.Series([11.0, None, 33.0])

    assert list(spend_basis(local, group)) == [11.0, 20.0, 33.0]
