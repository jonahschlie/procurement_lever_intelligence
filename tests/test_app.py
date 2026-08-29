"""Smoke tests for the Streamlit shell.

AppTest cannot drive ``st.file_uploader``, so these cover rendering: they guard
against import errors and misused Streamlit APIs, which unit tests never reach.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from agents.schema_mapping import ProposedMapping, SchemaMappingProposal
from core.canonical import CANONICAL_FIELDS
from core.config import RAW_PREVIEW_ROWS
from core.run import create_run
from ingestion.storage import StagedUpload, store_uploads
from mapping.schema_mapping import run_schema_mapping
from tests.conftest import FakeClient

APP_PATH = str(Path(__file__).parent.parent / "app.py")


def _app(**session):
    app = AppTest.from_file(APP_PATH, default_timeout=30)
    for key, value in session.items():
        app.session_state[key] = value
    return app.run()


def _mapping_page(**session):
    """Reach the mapping screen the way the app itself does."""
    return _app(switch_to="schema_mapping", **session)


def test_start_page_greets_and_explains(run_root):
    app = _app()

    assert not app.exception
    assert app.title[0].value == "Procurement Lever Intelligence"
    assert "value creation" in " ".join(block.value for block in app.markdown)
    assert any("No run started yet" in caption.value for caption in app.sidebar.caption)


def test_mapping_page_is_empty_without_a_run(run_root):
    app = _mapping_page()

    assert not app.exception
    assert app.title[0].value == "Schema Mapping"
    assert "No mapping yet" in app.info[0].value


def test_mapping_page_shows_the_table_and_the_raw_rows(run_root, sap_csv):
    run_id = create_run().run_id
    store_uploads(run_id, [StagedUpload(sap_csv, "sap_export.csv")])
    run_schema_mapping(
        run_id,
        client=FakeClient(
            SchemaMappingProposal(
                mappings=[
                    ProposedMapping(
                        canonical_field="supplier",
                        source_column="Vendor",
                        confidence=0.95,
                        comment="Values are company names.",
                    ),
                    ProposedMapping(
                        canonical_field="currency",
                        source_column="Currency",
                        confidence=0.4,
                        comment="Only one distinct value.",
                    ),
                ]
            )
        ),
    )

    app = _mapping_page(run_id=run_id)

    assert not app.exception

    # AppTest surfaces st.data_editor as a dataframe element: [0] is the mapping
    # table, [1] the raw preview underneath it.
    mapping, raw = (element.value for element in app.dataframe)

    assert len(mapping) == len(CANONICAL_FIELDS)
    assert mapping.loc[1, "Canonical field"] == "Supplier"
    assert mapping.loc[1, "Source column"] == "Vendor"
    assert mapping.loc[1, "Status"] == "OK"
    # Mapped, but below the threshold, so it is still called out for review.
    assert mapping.loc[5, "Canonical field"] == "Currency"
    assert mapping.loc[5, "Status"] == "Review"
    # Never proposed, and required, so it shows as a gap rather than a silent blank.
    assert mapping.loc[0, "Status"] == "Missing"

    assert len(raw) == min(RAW_PREVIEW_ROWS, 5)
    assert raw.loc[0, "Vendor"] == "Müller Logistik GmbH"
