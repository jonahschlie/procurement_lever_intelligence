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
from mapping.schema_mapping import confirm_mapping, run_schema_mapping
from tests.conftest import FakeClient
from profiling.data_profiling import confirm_profiling, run_profiling
from transform.canonical_table import build_canonical_table
from transform.rule_engine import run_rule_engine
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
    # The run block is gone: the sidebar is controls only.
    assert not app.sidebar.code


def test_start_page_keeps_the_controls_in_the_sidebar(run_root):
    app = _app()

    assert not app.exception
    assert len(app.sidebar.file_uploader) == 1
    assert [button.label for button in app.sidebar.button] == ["Start analysis"]
    # No preview: the workbook review is the first look at the data.
    assert not app.dataframe
    assert "Upload an ERP export in the sidebar" in app.info[0].value


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
    assert app.subheader[0].value == "portfolio.xlsx"

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


def _mapped_run(portfolio_xlsx):
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
    return run_id


def test_mapping_page_shows_the_table_and_the_raw_rows(run_root, portfolio_xlsx):
    run_id = _mapped_run(portfolio_xlsx)

    app = _page("schema_mapping", run_id=run_id)

    assert not app.exception

    # AppTest surfaces st.data_editor as a dataframe element: [0] is the mapping
    # table, [1] the raw preview underneath it.
    mapping, raw = (element.value for element in app.dataframe)

    # Looked up by field rather than by row number, so adding a canonical field
    # later does not silently shift what this asserts.
    rows = mapping.set_index("Canonical field")

    assert len(mapping) == len(CANONICAL_FIELDS)
    assert rows.loc["Supplier", "Source column"] == "Name of Supplier"
    assert rows.loc["Supplier", "Status"] == "OK"
    # Mapped, but below the threshold, so it is still called out for review.
    assert rows.loc["Currency", "Status"] == "Review"
    # Never proposed, and required, so it shows as a gap rather than a silent blank.
    assert rows.loc["Company", "Status"] == "Missing"
    # Not required, so an empty optional field is not dressed up as a problem.
    assert rows.loc["Company Name", "Status"] == "Not mapped"

    # Only the transactions sheet was mapped, not the FX or supplier tables.
    assert len(raw) == min(RAW_PREVIEW_ROWS, 5)
    assert raw.loc[0, "Name of Supplier"] == "Atlas Freight AB"


def test_mapping_page_offers_one_confirm_button(run_root, portfolio_xlsx):
    run_id = _mapped_run(portfolio_xlsx)

    app = _page("schema_mapping", run_id=run_id)

    assert not app.exception
    assert [button.label for button in app.button] == ["Confirm mapping and continue"]


def test_canonical_table_page_is_empty_without_a_table(run_root):
    app = _page("canonical_table")

    assert not app.exception
    assert app.title[0].value == "Canonical Table"
    assert "No canonical table yet" in app.info[0].value


def test_canonical_table_page_reports_what_was_built(run_root, portfolio_xlsx):
    run_id = _mapped_run(portfolio_xlsx)
    confirm_mapping(run_id, {})
    build_canonical_table(run_id)

    app = _page("canonical_table", run_id=run_id)

    assert not app.exception
    assert [metric.value for metric in app.metric] == ["5", "29", "1"]

    contributions, preview = (element.value for element in app.dataframe)
    assert contributions.loc[0, "Source"] == "portfolio.xlsx"
    assert contributions.loc[0, "Rows"] == 5
    assert preview.loc[0, "supplier"] == "Atlas Freight AB"
    # Unmapped source columns are kept rather than dropped.
    assert preview.loc[0, "extra_Company Name"] == "Northwind Nordics AB"


def test_start_page_asks_for_nothing_but_the_files(run_root):
    app = _app()

    assert not app.exception
    # No per-file company input: the group is the same for every export.
    assert not app.sidebar.text_input


def _canonical_run(portfolio_xlsx):
    run_id = _mapped_run(portfolio_xlsx)
    confirm_mapping(run_id, {})
    build_canonical_table(run_id)
    return run_id


def test_data_quality_page_is_empty_without_a_report(run_root):
    app = _page("data_quality")

    assert not app.exception
    assert app.title[0].value == "Data Quality"
    assert "No quality report yet" in app.info[0].value


def test_data_quality_page_shows_findings_and_asks_before_excluding(run_root, portfolio_xlsx):
    run_id = _canonical_run(portfolio_xlsx)
    run_profiling(run_id)

    app = _page("data_quality", run_id=run_id)

    assert not app.exception
    findings = app.dataframe[0].value
    assert "Check" in findings.columns
    assert len(findings) > 0
    # Nothing is applied until the user says so.
    assert [button.label for button in app.button] == ["Apply rules"]


def test_data_quality_page_reports_the_outcome_once_applied(run_root, portfolio_xlsx):
    run_id = _canonical_run(portfolio_xlsx)
    run_profiling(run_id)
    confirm_profiling(run_id)
    run_rule_engine(run_id)

    app = _page("data_quality", run_id=run_id)

    assert not app.exception
    labels = [metric.label for metric in app.metric]
    assert labels == ["Spend before", "Spend after exclusions", "Rows excluded"]
