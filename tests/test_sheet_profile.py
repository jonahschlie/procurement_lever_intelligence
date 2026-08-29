from core.models import ReadOptions
from ingestion.readers import file_options
from ingestion.sheet_profile import best_table_sheet, profile_sheets


def _by_name(data: bytes):
    return {p.name: p for p in profile_sheets(data, "xlsx", ReadOptions())}


def test_separates_tables_from_prose(portfolio_xlsx):
    profiles = _by_name(portfolio_xlsx)

    assert [name for name, p in profiles.items() if p.looks_like_table] == [
        "3. Spend Data",
        "4. Supplier Master",
        "5. FX",
    ]
    assert not profiles["1. Brief"].looks_like_table
    assert not profiles["2. How to Submit"].looks_like_table


def test_the_header_check_is_what_rejects_prose(portfolio_xlsx):
    brief = _by_name(portfolio_xlsx)["1. Brief"]

    # Fill ratio and rectangularity are both high enough on their own -- a narrow
    # cover sheet looks rectangular. What gives it away is the title row, which
    # fills one of two columns where a real header fills every column it spans.
    assert brief.fill_ratio >= 0.5
    assert brief.rectangularity >= 0.7
    assert not brief.has_header_row


def test_transaction_sheet_is_recognisable_by_dates_and_amounts(portfolio_xlsx):
    profiles = _by_name(portfolio_xlsx)

    spend = profiles["3. Spend Data"]
    assert (spend.has_date_column, spend.has_numeric_column) == (True, True)
    # The lookup tables are tables, but neither carries per-row dates.
    assert not profiles["4. Supplier Master"].has_date_column
    assert not profiles["5. FX"].has_date_column


def test_best_table_sheet_skips_the_cover_letter(portfolio_xlsx):
    assert best_table_sheet(profile_sheets(portfolio_xlsx, "xlsx", ReadOptions())) == "3. Spend Data"


def test_header_and_samples_are_captured(portfolio_xlsx):
    fx = _by_name(portfolio_xlsx)["5. FX"]

    assert fx.header == ["Currency", "Rate to EUR", "Note"]
    assert fx.sample_rows[0][:2] == ["SEK", "0.0872"]
    assert fx.rows == 3


def test_csv_is_a_single_unnamed_table(sap_csv):
    profiles = profile_sheets(sap_csv, "csv", file_options(sap_csv, "csv"))

    assert len(profiles) == 1
    assert profiles[0].name == ""
    assert profiles[0].looks_like_table
    assert profiles[0].header[0] == "Vendor"
    assert best_table_sheet(profiles) is None  # no sheet to name for a flat file
