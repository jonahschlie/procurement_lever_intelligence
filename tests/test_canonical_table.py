import pandas as pd

from agents.schema_mapping import ProposedMapping, SchemaMappingProposal
from core.canonical import CANONICAL_FIELDS
from core.run import create_run, load_run, run_path
from core.table import load_table, load_table_meta
from ingestion.storage import StagedUpload, store_files
from mapping.schema_mapping import confirm_mapping, run_schema_mapping
from tests.conftest import FakeClient
from transform.canonical_table import (
    BASE_COLUMNS,
    CANONICAL_COLUMNS,
    build_canonical_table,
    load_report,
)
from triage.workbook_triage import confirm_triage, run_workbook_triage

SAP_MAPPING = (
    ("company", "Company Code"),
    ("supplier", "Vendor"),
    ("supplier_id", "Vendor ID"),
    ("amount_local", "Amount LC"),
    ("currency", "Currency"),
    ("posting_date", "Posting Date"),
    ("invoice_number", "Document Number"),
    ("gl_description", "Account Description"),
)


def _proposal(pairs):
    return SchemaMappingProposal(
        mappings=[
            ProposedMapping(
                canonical_field=field, source_column=column, confidence=0.9, comment="x"
            )
            for field, column in pairs
        ]
    )


def _build(uploads, pairs=SAP_MAPPING):
    run_id = create_run().run_id
    store_files(run_id, list(uploads))
    run_workbook_triage(run_id)
    confirm_triage(run_id)
    run_schema_mapping(run_id, client=FakeClient(_proposal(pairs)))
    confirm_mapping(run_id, {})
    return run_id, build_canonical_table(run_id)


def test_columns_are_canonical_and_complete(run_root, sap_csv):
    run_id, report = _build([StagedUpload(sap_csv, "sap_export.csv")])
    table = load_table(run_id)

    assert list(table.columns)[: len(BASE_COLUMNS)] == list(BASE_COLUMNS)
    # Every canonical field exists, including the ones nothing was mapped to.
    assert set(CANONICAL_COLUMNS) == {field.key for field in CANONICAL_FIELDS}
    assert (table["cost_center"] == "").all()
    assert report.column_names == list(table.columns)


def test_unmapped_source_columns_are_kept(run_root, sap_csv):
    run_id, report = _build([StagedUpload(sap_csv, "sap_export.csv")])
    table = load_table(run_id)

    # Nothing was mapped to it, but it is still in the table rather than lost.
    assert "extra_Vendor ID" not in table.columns  # this one was mapped
    assert report.contributions[0].extra_columns == []

    pairs = tuple((f, c) for f, c in SAP_MAPPING if f != "gl_description")
    run_id, report = _build([StagedUpload(sap_csv, "sap_export.csv")], pairs)
    table = load_table(run_id)

    assert report.contributions[0].extra_columns == ["Account Description"]
    assert table.loc[0, "extra_Account Description"] == "Frachtkosten"
    assert (table["gl_description"] == "").all()


def test_spare_columns_of_different_datasets_are_unioned(run_root, sap_csv, oracle_csv):
    pairs = (("supplier", "Vendor"),)

    run_id, _ = _build(
        [StagedUpload(sap_csv, "sap_export.csv"), StagedUpload(oracle_csv, "oracle_export.csv")],
        pairs,
    )
    table = load_table(run_id)

    # A column only one export has is empty for the other's rows, never absent.
    assert "extra_Currency" in table.columns
    assert "extra_Currency Code" in table.columns
    sap = table[table["dataset_id"] == "01_sap_export"]
    oracle = table[table["dataset_id"] == "02_oracle_export"]
    assert (sap["extra_Currency"] == "EUR").all()
    assert (sap["extra_Currency Code"] == "").all()
    assert (oracle["extra_Currency Code"] == "USD").all()


def test_values_are_renamed_not_converted(run_root, sap_csv):
    run_id, _ = _build([StagedUpload(sap_csv, "sap_export.csv")])
    table = load_table(run_id)

    assert table.loc[0, "supplier"] == "Müller Logistik GmbH"
    # The two values that any type inference would have destroyed.
    assert table.loc[0, "supplier_id"] == "0000123456"
    assert table.loc[0, "amount_local"] == "1.250,00"
    assert table.loc[3, "amount_local"] == "-450,00"


def test_provenance_points_back_at_the_source_row(run_root, sap_csv):
    run_id, _ = _build([StagedUpload(sap_csv, "sap_export.csv")])
    table = load_table(run_id)

    assert table.loc[0, "dataset_id"] == "01_sap_export"
    assert table.loc[0, "source_file"] == "sap_export.csv"
    assert table.loc[0, "source_sheet"] == ""
    # Row 1 of the data is line 2 of the file, because line 1 is the header.
    assert list(table["source_row"]) == ["2", "3", "4", "5", "6"]


def test_company_and_company_name_are_separate_fields(run_root, sap_csv):
    run_id, _ = _build([StagedUpload(sap_csv, "sap_export.csv")])
    table = load_table(run_id)

    assert list(table["company"])[:2] == ["DE01", "DE01"]
    # The SAP fixture carries only a code, so the readable name stays empty.
    assert (table["company_name"] == "").all()
    assert "company_name" in CANONICAL_COLUMNS


def test_datasets_are_stacked_into_one_table(run_root, sap_csv, oracle_csv):
    run_id, report = _build(
        [
            StagedUpload(sap_csv, "sap_export.csv"),
            StagedUpload(oracle_csv, "oracle_export.csv"),
        ]
    )
    table = load_table(run_id)

    assert len(table) == 9  # 5 + 4
    assert report.row_count == 9
    assert list(table["dataset_id"].unique()) == ["01_sap_export", "02_oracle_export"]
    assert [c.row_count for c in report.contributions] == [5, 4]
    # The Oracle export has none of the SAP column names, so its canonical fields are empty.
    oracle = table[table["dataset_id"] == "02_oracle_export"]
    assert (oracle["supplier"] == "").all()
    assert (oracle["company"] == "").all()


def test_report_lists_mapped_and_unmapped_fields(run_root, sap_csv):
    _, report = _build([StagedUpload(sap_csv, "sap_export.csv")])
    contribution = report.contributions[0]

    assert set(contribution.mapped_fields) == {field for field, _ in SAP_MAPPING}
    assert "cost_center" in contribution.unmapped_fields
    assert len(contribution.mapped_fields) + len(contribution.unmapped_fields) == len(
        CANONICAL_FIELDS
    )


def test_step_and_table_metadata_are_recorded(run_root, sap_csv):
    run_id, _ = _build([StagedUpload(sap_csv, "sap_export.csv")])

    assert [s.step for s in load_run(run_id).steps] == [
        "ingestion",
        "workbook_triage",
        "schema_mapping",
        "canonical_table",
    ]
    assert (run_path(run_id) / "04_canonical_table" / "canonicalization.json").is_file()

    meta = load_table_meta(run_id)
    assert meta.row_count == 5
    assert [revision.step for revision in meta.revisions] == ["canonical_table"]

    log = (run_path(run_id) / "logs" / "run.log").read_text(encoding="utf-8")
    assert "canonical table built: 5 rows from 1 dataset(s)" in log


def test_empty_mapping_still_produces_the_schema(run_root, sap_csv):
    run_id, report = _build([StagedUpload(sap_csv, "sap_export.csv")], pairs=())
    table = load_table(run_id)

    assert len(table) == 5
    assert list(table.columns)[: len(BASE_COLUMNS)] == list(BASE_COLUMNS)
    assert report.contributions[0].mapped_fields == []
    # Nothing mapped means every source column is carried as a spare one.
    assert len(report.contributions[0].extra_columns) == 8
    assert isinstance(table, pd.DataFrame)
