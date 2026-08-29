import hashlib

from agents.workbook_triage import WorkbookTriageProposal
from core.run import create_run, load_run, run_path
from ingestion.readers import read_tabular
from ingestion.storage import (
    StagedUpload,
    load_dataframe,
    load_file_manifests,
    read_source,
    store_files,
)
from tests.conftest import FakeClient
from triage.workbook_triage import confirm_triage, load_datasets, run_workbook_triage


def test_stores_files_with_a_running_prefix(run_root, sap_csv, oracle_csv):
    run_id = create_run().run_id

    manifests = store_files(
        run_id,
        [
            StagedUpload(sap_csv, "sap_export.csv", "Alpha GmbH"),
            StagedUpload(oracle_csv, "oracle_export.csv"),
        ],
    )

    step = run_path(run_id) / "01_ingestion"
    assert [m.stored_filename for m in manifests] == ["01_sap_export.csv", "02_oracle_export.csv"]
    assert (step / "01_sap_export.csv").read_bytes() == sap_csv
    assert manifests[0].content_hash == hashlib.sha256(sap_csv).hexdigest()
    assert manifests[0].company_label == "Alpha GmbH"
    assert manifests[1].company_label is None
    assert manifests[0].read_options.delimiter == ";"
    assert manifests[0].sheet_names == []
    assert load_file_manifests(run_id) == manifests


def test_identical_filenames_do_not_overwrite_each_other(run_root, sap_csv, oracle_csv):
    run_id = create_run().run_id

    store_files(run_id, [StagedUpload(sap_csv, "export.csv"), StagedUpload(oracle_csv, "export.csv")])

    step = run_path(run_id) / "01_ingestion"
    assert (step / "01_export.csv").read_bytes() == sap_csv
    assert (step / "02_export.csv").read_bytes() == oracle_csv


def test_workbook_records_its_sheet_names(run_root, portfolio_xlsx):
    run_id = create_run().run_id

    manifest = store_files(run_id, [StagedUpload(portfolio_xlsx, "portfolio.xlsx")])[0]

    assert manifest.sheet_names[0] == "1. Brief"
    assert len(manifest.sheet_names) == 5
    # No sheet is bound at file level -- triage decides that.
    assert manifest.read_options.sheet is None
    assert read_source(run_id, manifest.stored_filename) == portfolio_xlsx


def test_records_the_step(run_root, sap_csv):
    run_id = create_run().run_id

    store_files(run_id, [StagedUpload(sap_csv, "sap_export.csv")])

    steps = load_run(run_id).steps
    assert [s.step for s in steps] == ["ingestion"]
    assert steps[0].artifacts == ["01_ingestion/01_sap_export.csv", "01_ingestion/ingestion.json"]


def test_datasets_reproduce_the_original_parse(run_root, sap_csv):
    run_id = create_run().run_id
    store_files(run_id, [StagedUpload(sap_csv, "sap_export.csv")])
    run_workbook_triage(run_id)
    confirm_triage(run_id)

    dataset = load_datasets(run_id)[0]
    expected, _ = read_tabular(sap_csv, "sap_export.csv")
    frame = load_dataframe(run_id, dataset)

    assert frame.equals(expected)
    # Type inference anywhere in ingestion would have destroyed both of these.
    assert frame.loc[0, "Vendor"] == "Müller Logistik GmbH"
    assert frame.loc[0, "Vendor ID"] == "0000123456"
    assert frame.loc[0, "Amount LC"] == "1.250,00"


def test_each_sheet_becomes_its_own_dataset(run_root, portfolio_xlsx):
    run_id = create_run().run_id
    store_files(run_id, [StagedUpload(portfolio_xlsx, "portfolio.xlsx")])
    run_workbook_triage(run_id, client=FakeClient(WorkbookTriageProposal(sheets=[])))
    confirm_triage(run_id)

    # Nothing was classified, so every table sheet is 'unknown' -- still one dataset each,
    # all reading from the same stored file.
    datasets = load_datasets(run_id)
    assert len(datasets) == 3
    assert len({d.stored_filename for d in datasets}) == 1
    assert {d.sheet for d in datasets} == {"3. Spend Data", "4. Supplier Master", "5. FX"}

    fx = next(d for d in datasets if d.sheet == "5. FX")
    assert list(load_dataframe(run_id, fx).columns) == ["Currency", "Rate to EUR", "Note"]


def test_runs_are_independent(run_root, sap_csv):
    first = create_run().run_id
    store_files(first, [StagedUpload(sap_csv, "sap_export.csv")])
    second = create_run().run_id
    store_files(second, [StagedUpload(sap_csv, "sap_export.csv")])

    assert first != second
    assert (run_path(first) / "01_ingestion" / "01_sap_export.csv").is_file()
    assert (run_path(second) / "01_ingestion" / "01_sap_export.csv").is_file()
