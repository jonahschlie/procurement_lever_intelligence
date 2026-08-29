import hashlib

from core.run import create_run, load_run, run_path
from ingestion.readers import read_tabular
from ingestion.storage import StagedUpload, load_dataframe, load_manifests, store_uploads


def _run_and_store(*items) -> tuple[str, list]:
    run_id = create_run().run_id
    return run_id, store_uploads(run_id, list(items))


def test_files_land_in_the_ingestion_step_with_a_running_prefix(run_root, sap_csv, oracle_csv):
    run_id, manifests = _run_and_store(
        StagedUpload(sap_csv, "sap_export.csv", "Alpha GmbH"),
        StagedUpload(oracle_csv, "oracle_export.csv"),
    )

    step = run_path(run_id) / "01_ingestion"
    assert [m.stored_filename for m in manifests] == [
        "01_sap_export.csv",
        "02_oracle_export.csv",
    ]
    assert (step / "01_sap_export.csv").read_bytes() == sap_csv
    assert (step / "02_oracle_export.csv").read_bytes() == oracle_csv
    assert manifests[0].content_hash == hashlib.sha256(sap_csv).hexdigest()
    assert manifests[0].company_label == "Alpha GmbH"
    assert manifests[1].company_label is None


def test_identical_filenames_do_not_overwrite_each_other(run_root, sap_csv, oracle_csv):
    run_id, manifests = _run_and_store(
        StagedUpload(sap_csv, "export.csv"),
        StagedUpload(oracle_csv, "export.csv"),
    )

    step = run_path(run_id) / "01_ingestion"
    assert (step / "01_export.csv").read_bytes() == sap_csv
    assert (step / "02_export.csv").read_bytes() == oracle_csv
    assert [m.original_filename for m in manifests] == ["export.csv", "export.csv"]


def test_artifact_holds_every_manifest(run_root, sap_csv, dynamics_xlsx):
    run_id, manifests = _run_and_store(
        StagedUpload(sap_csv, "sap_export.csv"),
        StagedUpload(dynamics_xlsx, "dynamics_export.xlsx", sheet="Transactions"),
    )

    assert (run_path(run_id) / "01_ingestion" / "ingestion.json").is_file()
    assert load_manifests(run_id) == manifests


def test_run_manifest_records_the_step_and_its_artifacts(run_root, sap_csv):
    run_id, _ = _run_and_store(StagedUpload(sap_csv, "sap_export.csv"))

    steps = load_run(run_id).steps
    assert [step.step for step in steps] == ["ingestion"]
    assert steps[0].artifacts == [
        "01_ingestion/01_sap_export.csv",
        "01_ingestion/ingestion.json",
    ]


def test_log_records_one_line_per_file(run_root, sap_csv, oracle_csv):
    run_id, _ = _run_and_store(
        StagedUpload(sap_csv, "sap_export.csv"),
        StagedUpload(oracle_csv, "oracle_export.csv"),
    )

    log = (run_path(run_id) / "logs" / "run.log").read_text(encoding="utf-8")
    assert log.count("stored sap_export.csv as 01_sap_export.csv") == 1
    assert log.count("stored oracle_export.csv as 02_oracle_export.csv") == 1
    assert log.count("ingestion complete: 2 file(s)") == 1


def test_load_dataframe_matches_the_original_parse(run_root, sap_csv):
    run_id, manifests = _run_and_store(StagedUpload(sap_csv, "sap_export.csv"))
    expected, _ = read_tabular(sap_csv, "sap_export.csv")

    frame = load_dataframe(run_id, manifests[0])

    assert frame.equals(expected)
    # Type inference anywhere in ingestion would have destroyed both of these.
    assert frame.loc[0, "Vendor"] == "Müller Logistik GmbH"
    assert frame.loc[0, "Vendor ID"] == "0000123456"
    assert frame.loc[0, "Amount LC"] == "1.250,00"


def test_excel_sheet_choice_survives_the_round_trip(run_root, dynamics_xlsx):
    run_id, manifests = _run_and_store(
        StagedUpload(dynamics_xlsx, "dynamics_export.xlsx", sheet="Transactions")
    )

    assert manifests[0].read_options.sheet == "Transactions"
    assert load_dataframe(run_id, manifests[0]).loc[0, "Partner ID"] == "0091"


def test_runs_are_independent(run_root, sap_csv):
    first_id, _ = _run_and_store(StagedUpload(sap_csv, "sap_export.csv"))
    second_id, _ = _run_and_store(StagedUpload(sap_csv, "sap_export.csv"))

    assert first_id != second_id
    assert (run_path(first_id) / "01_ingestion" / "01_sap_export.csv").is_file()
    assert (run_path(second_id) / "01_ingestion" / "01_sap_export.csv").is_file()
