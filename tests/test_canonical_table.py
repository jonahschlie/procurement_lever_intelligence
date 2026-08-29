import pandas as pd

from agents.schema_mapping import ProposedMapping, SchemaMappingProposal
from core.canonical import CANONICAL_FIELDS
from core.run import create_run, load_run, run_path
from core.table import load_table, load_table_meta
from ingestion.storage import StagedUpload, store_files
from mapping.schema_mapping import confirm_mapping, run_schema_mapping
from tests.conftest import FakeClient
from transform.canonical_table import (
    CANONICAL_COLUMNS,
    COLUMN_ORDER,
    build_canonical_table,
    load_report,
)
from triage.workbook_triage import confirm_triage, run_workbook_triage

SAP_MAPPING = (
    ("supplier", "Vendor"),
    ("supplier_id", "Vendor ID"),
    ("amount_local", "Amount LC"),
    ("currency", "Currency"),
    ("posting_date", "Posting Date"),
    ("invoice_number", "Document Number"),
    ("gl_description", "Account Description"),
    ("company", "Company Code"),
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
    run_id, report = _build([StagedUpload(sap_csv, "sap_export.csv", "Alpha GmbH")])
    table = load_table(run_id)

    assert list(table.columns) == list(COLUMN_ORDER)
    # Every canonical field exists, including the ones nothing was mapped to.
    assert set(CANONICAL_COLUMNS) == {field.key for field in CANONICAL_FIELDS}
    assert (table["cost_center"] == "").all()
    assert report.column_names == list(COLUMN_ORDER)


def test_values_are_renamed_not_converted(run_root, sap_csv):
    run_id, _ = _build([StagedUpload(sap_csv, "sap_export.csv", "Alpha GmbH")])
    table = load_table(run_id)

    assert table.loc[0, "supplier"] == "Müller Logistik GmbH"
    # The two values that any type inference would have destroyed.
    assert table.loc[0, "supplier_id"] == "0000123456"
    assert table.loc[0, "amount_local"] == "1.250,00"
    assert table.loc[3, "amount_local"] == "-450,00"


def test_provenance_points_back_at_the_source_row(run_root, sap_csv):
    run_id, _ = _build([StagedUpload(sap_csv, "sap_export.csv", "Alpha GmbH")])
    table = load_table(run_id)

    assert table.loc[0, "dataset_id"] == "01_sap_export"
    assert table.loc[0, "source_file"] == "sap_export.csv"
    assert table.loc[0, "source_sheet"] == ""
    assert table.loc[0, "company_label"] == "Alpha GmbH"
    # Row 1 of the data is line 2 of the file, because line 1 is the header.
    assert list(table["source_row"]) == ["2", "3", "4", "5", "6"]


def test_company_comes_from_the_data_when_there_is_a_column(run_root, sap_csv):
    run_id, report = _build([StagedUpload(sap_csv, "sap_export.csv", "Alpha GmbH")])
    table = load_table(run_id)

    assert list(table["company"])[:2] == ["DE01", "DE01"]
    assert (table["company_source"] == "data").all()
    assert report.contributions[0].company_source_counts == {"data": 5}


def test_upload_label_fills_in_when_the_data_has_no_company(run_root, sap_csv):
    pairs = tuple((f, c) for f, c in SAP_MAPPING if f != "company")

    run_id, report = _build([StagedUpload(sap_csv, "sap_export.csv", "Alpha GmbH")], pairs)
    table = load_table(run_id)

    assert (table["company"] == "Alpha GmbH").all()
    assert (table["company_source"] == "upload_label").all()
    assert report.contributions[0].company_source_counts == {"upload_label": 5}


def test_company_stays_missing_without_a_column_or_a_label(run_root, sap_csv):
    pairs = tuple((f, c) for f, c in SAP_MAPPING if f != "company")

    run_id, _ = _build([StagedUpload(sap_csv, "sap_export.csv")], pairs)
    table = load_table(run_id)

    assert (table["company"] == "").all()
    assert (table["company_source"] == "missing").all()


def test_datasets_are_stacked_into_one_table(run_root, sap_csv, oracle_csv):
    run_id, report = _build(
        [
            StagedUpload(sap_csv, "sap_export.csv", "Alpha GmbH"),
            StagedUpload(oracle_csv, "oracle_export.csv", "Beta Inc"),
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
    assert (oracle["company"] == "Beta Inc").all()


def test_report_lists_mapped_and_unmapped_fields(run_root, sap_csv):
    _, report = _build([StagedUpload(sap_csv, "sap_export.csv", "Alpha GmbH")])
    contribution = report.contributions[0]

    assert set(contribution.mapped_fields) == {field for field, _ in SAP_MAPPING}
    assert "cost_center" in contribution.unmapped_fields
    assert len(contribution.mapped_fields) + len(contribution.unmapped_fields) == len(
        CANONICAL_FIELDS
    )


def test_step_and_table_metadata_are_recorded(run_root, sap_csv):
    run_id, _ = _build([StagedUpload(sap_csv, "sap_export.csv", "Alpha GmbH")])

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
    assert list(table.columns) == list(COLUMN_ORDER)
    assert report.contributions[0].mapped_fields == []
    assert isinstance(table, pd.DataFrame)
