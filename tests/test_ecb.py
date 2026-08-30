import pandas as pd
import pytest

from fx.ecb import daily_rates, parse_ecb_csv, rates_for

CSV = (
    "Date,USD,PLN,HUF,RON,\n"
    "2024-01-11,1.09,4.33,381.0,4.96,\n"
    "2024-01-12,1.09,4.35,382.0,4.97,\n"
    "2024-01-15,1.10,4.37,380.5,4.97,\n"
)


@pytest.fixture
def rates():
    return parse_ecb_csv(CSV)


def test_parses_the_shipped_layout_as_well_as_the_download():
    # The shipped file uses a lowercase 'date' and carries an EUR column;
    # the ECB download uses 'Date' and a trailing comma. Both must load.
    shipped = parse_ecb_csv("date,EUR,PLN\n2024-01-12,1.0,4.35\n")

    assert shipped.index.name == "Date"
    assert shipped.loc[pd.Timestamp("2024-01-12"), "PLN"] == 4.35


def test_the_shipped_history_covers_the_data(rates):
    from fx.ecb import load_reference_rates

    history = load_reference_rates()

    assert {"PLN", "HUF", "RON"} <= set(history.columns)
    # The bookings in hand are from 2024; the file must span them without gaps.
    year = history.loc["2024-01-01":"2024-12-31", ["PLN", "HUF", "RON"]]
    assert len(year) > 250
    assert not year.isna().any().any()


def test_parses_the_ecb_layout(rates):
    assert list(rates.columns) == ["USD", "PLN", "HUF", "RON"]
    assert rates.loc[pd.Timestamp("2024-01-12"), "PLN"] == 4.35


def test_conversion_direction_is_units_per_eur(rates):
    rate, _ = rates_for(rates, pd.Series(["PLN"]), pd.Series(pd.to_datetime(["2024-01-15"])))

    # 4.37 zloty buy one euro, so 100 PLN are about 22.88 EUR.
    assert round(100 / rate.iloc[0], 2) == 22.88


def test_weekends_use_the_last_published_rate(rates):
    daily = daily_rates(rates)

    # The 13th and 14th are the weekend after Friday the 12th.
    assert daily.loc[pd.Timestamp("2024-01-13"), "HUF"] == 382.0
    assert daily.loc[pd.Timestamp("2024-01-14"), "HUF"] == 382.0


def test_eur_is_one_by_definition(rates):
    rate, _ = rates_for(rates, pd.Series(["EUR"]), pd.Series(pd.to_datetime(["2024-01-13"])))

    assert rate.iloc[0] == 1.0


def test_unknown_currency_missing_date_and_out_of_range_get_no_rate(rates):
    rate, used = rates_for(
        rates,
        pd.Series(["XXX", "PLN", "PLN", ""]),
        pd.Series(pd.to_datetime(["2024-01-15", None, "2030-01-01", "2024-01-15"])),
    )

    assert rate.isna().all()
    assert used.isna().all()
