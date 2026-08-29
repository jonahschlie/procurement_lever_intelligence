"""Smoke test for the Streamlit shell.

AppTest cannot drive ``st.file_uploader``, so this only covers rendering: it guards
against import errors and misused Streamlit APIs, which unit tests never reach.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from ingestion.storage import save_upload

APP_PATH = str(Path(__file__).parent.parent / "app.py")


def test_renders_empty_state(data_root):
    app = AppTest.from_file(APP_PATH, default_timeout=30).run()

    assert not app.exception
    assert app.title[0].value == "Data Upload"
    assert "No datasets stored yet" in app.info[0].value


def test_lists_a_stored_dataset(data_root, sap_csv):
    save_upload(sap_csv, "sap_export.csv", company_label="Alpha GmbH")

    app = AppTest.from_file(APP_PATH, default_timeout=30).run()

    assert not app.exception
    table = app.dataframe[0].value
    assert list(table["Company"]) == ["Alpha GmbH"]
    assert list(table["Rows"]) == [5]
