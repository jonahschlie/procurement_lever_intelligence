import hashlib

import pytest

from ingestion.readers import read_tabular
from ingestion.storage import (
    delete_upload,
    list_uploads,
    load_dataframe,
    load_manifest,
    save_upload,
)


def test_stores_original_bytes_and_manifest(data_root, sap_csv):
    manifest, duplicate = save_upload(sap_csv, "sap_export.csv", company_label="Alpha GmbH")

    assert duplicate is False
    stored = data_root / "uploads" / manifest.upload_id
    assert (stored / "source.csv").read_bytes() == sap_csv
    assert manifest.content_hash == hashlib.sha256(sap_csv).hexdigest()
    assert manifest.company_label == "Alpha GmbH"
    assert manifest.row_count == 5
    assert manifest.column_names[0] == "Vendor"
    assert manifest.read_options.delimiter == ";"
    assert load_manifest(manifest.upload_id) == manifest


def test_blank_company_label_is_stored_as_none(data_root, sap_csv):
    manifest, _ = save_upload(sap_csv, "sap_export.csv", company_label="")

    assert manifest.company_label is None


def test_identical_content_is_not_stored_twice(data_root, sap_csv):
    first, _ = save_upload(sap_csv, "sap_export.csv")
    second, duplicate = save_upload(sap_csv, "renamed.csv")

    assert duplicate is True
    assert second.upload_id == first.upload_id
    assert len(list_uploads()) == 1


def test_load_dataframe_matches_the_original_parse(data_root, sap_csv):
    manifest, _ = save_upload(sap_csv, "sap_export.csv")
    expected, _ = read_tabular(sap_csv, "sap_export.csv")

    assert load_dataframe(manifest.upload_id).equals(expected)


def test_excel_sheet_choice_survives_the_round_trip(data_root, dynamics_xlsx):
    manifest, _ = save_upload(dynamics_xlsx, "dynamics_export.xlsx", sheet="Transactions")

    assert manifest.read_options.sheet == "Transactions"
    assert load_dataframe(manifest.upload_id).loc[0, "Partner ID"] == "0091"


def test_lists_uploads_newest_first(data_root, sap_csv, oracle_csv):
    older, _ = save_upload(sap_csv, "sap_export.csv")
    newer, _ = save_upload(oracle_csv, "oracle_export.csv")

    # Both land in the same second, so ordering must come from uploaded_at.
    listed = [manifest.upload_id for manifest in list_uploads()]
    assert listed == [newer.upload_id, older.upload_id]


def test_delete_removes_the_upload(data_root, sap_csv):
    manifest, _ = save_upload(sap_csv, "sap_export.csv")

    delete_upload(manifest.upload_id)

    assert list_uploads() == []
    assert not (data_root / "uploads" / manifest.upload_id).exists()


def test_unknown_upload_is_rejected(data_root):
    with pytest.raises(FileNotFoundError):
        load_dataframe("does-not-exist")


def test_no_uploads_directory_yields_empty_list(data_root):
    assert list_uploads() == []
