"""Smoke tests for the Streamlit shell.

AppTest cannot drive ``st.file_uploader``, so these cover rendering: they guard
against import errors and misused Streamlit APIs, which unit tests never reach.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from agents.schema_mapping import ProposedMapping, SchemaMappingProposal
from agents.workbook_triage import ProposedRole, WorkbookTriageProposal
from core.canonical import CANONICAL_FIELDS
from core.config import RAW_PREVIEW_ROWS
from core.run import create_run
from ingestion.storage import StagedUpload, store_files
from mapping.schema_mapping import run_schema_mapping
from tests.conftest import FakeClient
from triage.workbook_triage import confirm_triage, run_workbook_triage

APP_PATH = str(Path(__file__).parent.parent / "app.py")


def _app(**session):
    app = AppTest.from_file(APP_PATH, default_timeout=30)
    for key, value in session.items():
        app.session_state[key] = value
    return app.run()


def _page(name, **session):
    """Reach a screen the way the app itself does."""
    return _app(switch_to=name, **session)


def _triaged_run(portfolio_xlsx):
    run_id = create_run().run_id
    store_files(run_id, [StagedUpload(portfolio_xlsx, "portfolio.xlsx")])
    run_workbook_triage(
        run_id,
        client=FakeClient(
            WorkbookTriageProposal(
                sheets=[
                    ProposedRole(
                        sheet="3. Spend Data",
                        role="transactions",
                        confidence=0.96,
                        comment="One booking per row.",
                    ),
                    ProposedRole(
                        sheet="5. FX", role="fx_rates", confidence=0.9, comment="Codes and rates."
                    ),
                    ProposedRole(
                        sheet="4. Supplier Master",
                        role="supplier_master",
                        confidence=0.88,
                        comment="Suppliers without amounts.",
                    ),
                ]
            )
        ),
    )
    return run_id


def test_start_page_greets_and_explains(run_root):
    app = _app()

    assert not app.exception
    assert app.title[0].value == "Procurement Lever Intelligence"
    assert "value creation" in " ".join(block.value for block in app.markdown)
    assert any("No run started yet" in caption.value for caption in app.sidebar.caption)


def test_review_page_is_empty_without_a_run(run_root):
    app = _page("workbook_review")

    assert not app.exception
    assert app.title[0].value == "Workbook Review"
    assert "Nothing to review yet" in app.info[0].value


def test_mapping_page_is_empty_without_a_run(run_root):
    app = _page("schema_mapping")

    assert not app.exception
    assert app.title[0].value == "Schema Mapping"
    assert "No mapping yet" in app.info[0].value


def test_review_page_lists_every_sheet_with_its_role(run_root, portfolio_xlsx):
    run_id = _triaged_run(portfolio_xlsx)

    app = _page("workbook_review", run_id=run_id)

    assert not app.exception
    table = app.dataframe[0].value
    assert list(table["Sheet"]) == [
        "1. Brief",
        "2. How to Submit",
        "3. Spend Data",
        "4. Supplier Master",
        "5. FX",
    ]
    assert list(table["Role"]) == [
        "documentation",
        "documentation",
        "transactions",
        "supplier_master",
        "fx_rates",
    ]
    assert table.loc[2, "Rows"] == 6


def test_mapping_page_shows_the_table_and_the_raw_rows(run_root, portfolio_xlsx):
    run_id = _triaged_run(portfolio_xlsx)
    confirm_triage(run_id)
    run_schema_mapping(
        run_id,
        client=FakeClient(
            SchemaMappingProposal(
                mappings=[
                    ProposedMapping(
                        canonical_field="supplier",
                        source_column="Name of Supplier",
                        confidence=0.95,
                        comment="Values are company names.",
                    ),
                    ProposedMapping(
                        canonical_field="currency",
                        source_column="Local Currency",
                        confidence=0.4,
                        comment="Few distinct values.",
                    ),
                ]
            )
        ),
    )

    app = _page("schema_mapping", run_id=run_id)

    assert not app.exception

    # AppTest surfaces st.data_editor as a dataframe element: [0] is the mapping
    # table, [1] the raw preview underneath it.
    mapping, raw = (element.value for element in app.dataframe)

    assert len(mapping) == len(CANONICAL_FIELDS)
    assert mapping.loc[1, "Canonical field"] == "Supplier"
    assert mapping.loc[1, "Source column"] == "Name of Supplier"
    assert mapping.loc[1, "Status"] == "OK"
    # Mapped, but below the threshold, so it is still called out for review.
    assert mapping.loc[5, "Canonical field"] == "Currency"
    assert mapping.loc[5, "Status"] == "Review"
    # Never proposed, and required, so it shows as a gap rather than a silent blank.
    assert mapping.loc[0, "Status"] == "Missing"

    # Only the transactions sheet was mapped, not the FX or supplier tables.
    assert len(raw) == min(RAW_PREVIEW_ROWS, 5)
    assert raw.loc[0, "Name of Supplier"] == "Atlas Freight AB"
