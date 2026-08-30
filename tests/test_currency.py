import pandas as pd
import pytest

from core.run import load_run, run_path
from core.table import load_table, load_table_meta, write_table
from fx.currency import load_report, run_currency
from fx.ecb import parse_ecb_csv
from profiling.data_profiling import confirm_profiling, run_profiling
from transform.rule_engine import run_rule_engine

CSV = (
    "Date,PLN,HUF,\n"
    "2024-01-12,4.00,380.0,\n"
    "2024-01-15,4.00,380.0,\n"
    "2024-02-09,4.20,400.0,\n"
    "2024-02-12,4.20,400.0,\n"
    "2024-03-01,4.10,390.0,\n"
    "2024-03-05,4.10,390.0,\n"
    "2024-04-01,4.00,380.0,\n"
    "2024-05-02,4.00,380.0,\n"
)


@pytest.fixture
def ruled(defective_run):
    run_profiling(defective_run)
    confirm_profiling(defective_run)
    run_rule_engine(defective_run)
    return defective_run


@pytest.fixture
def mixed_currency_run(ruled):
    # Rows 2 and 5 become Polish bookings so the conversion has work to do.
    table = load_table(ruled)
    table.loc[table["source_row"] == "2", "currency"] = "PLN"
    table.loc[table["source_row"] == "5", "currency"] = "PLN"
    write_table(ruled, table, "rule_engine")
    return ruled


def _rows(run_id):
    return load_table(run_id).set_index("source_row")


def test_converts_at_the_posting_dates_rate(mixed_currency_run):
    run_currency(mixed_currency_run, parse_ecb_csv(CSV))

    rows = _rows(mixed_currency_run)
    # 1000 PLN on 2024-01-15 at 4.00 -> 250 EUR.
    assert rows.loc["2", "fx_rate"] == 4.0
    assert rows.loc["2", "amount_eur"] == 250.0
    # -300 PLN on 2024-03-05 at 4.10.
    assert rows.loc["5", "amount_eur"] == pytest.approx(-300 / 4.1)


def test_eur_rows_pass_through_at_one(mixed_currency_run):
    run_currency(mixed_currency_run, parse_ecb_csv(CSV))

    rows = _rows(mixed_currency_run)
    assert rows.loc["3", "fx_rate"] == 1.0
    assert rows.loc["3", "amount_eur"] == 2000.0


def test_rows_without_a_usable_rate_are_flagged_not_guessed(ruled):
    # Row 9 becomes a PLN booking posted in 2099 -- beyond any published rate.
    # As EUR it would rightly convert at 1.0 whatever the date says.
    table = load_table(ruled)
    table.loc[table["source_row"] == "9", "currency"] = "PLN"
    write_table(ruled, table, "rule_engine")

    run_currency(ruled, parse_ecb_csv(CSV))

    rows = _rows(ruled)
    # Row 8 has an amount but no currency; row 9 has no rate to convert at.
    assert rows.loc["8", "flag_missing_fx_rate"]
    assert pd.isna(rows.loc["8", "amount_eur"])
    assert rows.loc["9", "flag_missing_fx_rate"]
    # No amount means nothing to flag.
    assert not rows.loc["11", "flag_missing_fx_rate"]


def test_spend_counts_net_with_credits_reported_alongside(mixed_currency_run):
    report = run_currency(mixed_currency_run, parse_ecb_csv(CSV))

    assert report.spend_net_eur == pytest.approx(
        report.spend_gross_eur - report.credit_volume_eur
    )
    assert report.credit_volume_eur > 0  # the -300 PLN credit note
    assert report.spend_net_eur < report.spend_gross_eur


def test_unconverted_group_amounts_are_called_out(mixed_currency_run):
    report = run_currency(mixed_currency_run, parse_ecb_csv(CSV))

    # The PLN rows carry group amounts identical to their local amounts.
    assert report.group_unconverted_rows == 2


def test_rates_are_frozen_into_the_run(mixed_currency_run):
    run_currency(mixed_currency_run, parse_ecb_csv(CSV))

    frozen = run_path(mixed_currency_run) / "07_currency" / "ecb_rates.csv"
    assert frozen.is_file()
    stored = pd.read_csv(frozen)
    assert list(stored.columns) == ["Date", "PLN"]  # only what the run needed


def test_no_row_is_removed_and_the_history_grows(mixed_currency_run):
    report = run_currency(mixed_currency_run, parse_ecb_csv(CSV))

    assert report.row_count == 12
    meta = load_table_meta(mixed_currency_run)
    assert meta.revisions[-1].step == "currency"
    assert "amount_eur" in meta.revisions[-1].columns_added
    assert [s.step for s in load_run(mixed_currency_run).steps] == [
        "profiling",
        "rule_engine",
        "currency",
    ]
    assert load_report(mixed_currency_run) == report


def test_breakdown_lists_each_currency(mixed_currency_run):
    report = run_currency(mixed_currency_run, parse_ecb_csv(CSV))

    entries = {entry.currency: entry for entry in report.breakdown}
    assert set(entries) == {"EUR", "PLN"}
    assert entries["PLN"].rows == 2
    assert entries["PLN"].sum_eur == pytest.approx(250.0 - 300 / 4.1)
