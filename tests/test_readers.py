import pytest

from ingestion.readers import (
    UnsupportedFileError,
    list_sheets,
    read_tabular,
    read_with_options,
)


def test_detects_semicolon_and_cp1252(sap_csv):
    frame, options = read_tabular(sap_csv, "sap_export.csv")

    assert options.delimiter == ";"
    assert options.encoding == "cp1252"
    assert list(frame.columns)[:2] == ["Vendor", "Vendor ID"]
    assert frame.loc[0, "Vendor"] == "Müller Logistik GmbH"


def test_preserves_leading_zeros_and_german_decimals(sap_csv):
    frame, _ = read_tabular(sap_csv, "sap_export.csv")

    # Type inference here would turn these into 123456 and 1250.0 respectively.
    assert frame.loc[0, "Vendor ID"] == "0000123456"
    assert frame.loc[0, "Amount LC"] == "1.250,00"
    assert frame.loc[3, "Amount LC"] == "-450,00"


def test_reads_comma_separated_utf8(oracle_csv):
    frame, options = read_tabular(oracle_csv, "oracle_export.csv")

    assert options.delimiter == ","
    assert options.encoding.startswith("utf-8")
    assert frame.loc[1, "Supplier Name"] == "Microsoft Corporation"
    assert len(frame) == 4


def test_excel_defaults_to_first_sheet(dynamics_xlsx):
    assert list_sheets(dynamics_xlsx) == ["Notes", "Transactions"]

    _, options = read_tabular(dynamics_xlsx, "dynamics_export.xlsx")
    assert options.sheet == "Notes"


def test_excel_reads_selected_sheet(dynamics_xlsx):
    frame, options = read_tabular(dynamics_xlsx, "dynamics_export.xlsx", sheet="Transactions")

    assert options.sheet == "Transactions"
    assert frame.loc[0, "Business Partner"] == "Nordwind Papier BV"
    assert frame.loc[0, "Partner ID"] == "0091"
    assert len(frame) == 3


def test_unknown_sheet_is_rejected(dynamics_xlsx):
    with pytest.raises(ValueError, match="not found"):
        read_tabular(dynamics_xlsx, "dynamics_export.xlsx", sheet="Missing")


def test_unsupported_extension_is_rejected():
    with pytest.raises(UnsupportedFileError):
        read_tabular(b"irrelevant", "export.txt")


def test_stored_options_reproduce_the_same_frame(sap_csv):
    frame, options = read_tabular(sap_csv, "sap_export.csv")

    assert read_with_options(sap_csv, "csv", options).equals(frame)
