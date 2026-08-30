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
    assert [metric.value for metric in app.metric] == ["5", "37", "1"]

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







def _ruled_run(portfolio_xlsx):
    """A run mapped fully enough that rows qualify for spend analysis."""
    from mapping.schema_mapping import load_artifact
    from profiling.data_profiling import confirm_profiling as _confirm
    from transform.rule_engine import run_rule_engine as _rules

    run_id = _mapped_run(portfolio_xlsx)
    dataset_id = load_artifact(run_id).datasets[0].dataset_id
    confirm_mapping(
        run_id,
        {
            dataset_id: {
                "amount_local": "Amount in Local Currency",
                "posting_date": "Posting Date",
                "company": "Company Code",
                "invoice_number": "Document Number",
                "gl_account": "G/L Account",
            }
        },
    )
    build_canonical_table(run_id)
    run_profiling(run_id)
    _confirm(run_id)
    _rules(run_id)
    return run_id




def _analysed_run(portfolio_xlsx):
    """A run taken through everything the review screen expects to find."""
    from agents.spend_addressability import AddressabilityProposal
    from agents.supplier_matching import SupplierMatchProposal
    from classification.spend_classification import run_spend_classification
    from fx.currency import run_currency
    from fx.ecb import parse_ecb_csv
    from profiling.data_profiling import confirm_profiling as _confirm
    from suppliers.normalization import run_supplier_normalization
    from transform.rule_engine import run_rule_engine as _rules

    run_id = _ruled_run(portfolio_xlsx)
    run_currency(
        run_id,
        parse_ecb_csv("Date,SEK,\n2025-01-10,11.50,\n2025-02-03,11.40,\n2025-03-03,11.30,\n"),
    )
    run_supplier_normalization(run_id, client=FakeClient(SupplierMatchProposal(verdicts=[])))
    run_spend_classification(run_id, client=FakeClient(AddressabilityProposal(verdicts=[])))
    return run_id


def test_review_screen_is_empty_before_the_analysis(run_root):
    app = _page("review")

    assert not app.exception
    assert app.title[0].value == "Review & Confirm"
    assert "Nothing to review yet" in app.info[0].value


def test_report_is_empty_before_anything_is_confirmed(run_root):
    app = _page("report")

    assert not app.exception
    assert app.title[0].value == "Data Quality Report"
    assert "No report yet" in app.info[0].value


def test_review_screen_gathers_every_decision_behind_one_button(run_root, portfolio_xlsx):
    run_id = _analysed_run(portfolio_xlsx)

    app = _page("review", run_id=run_id)

    assert not app.exception
    headings = [h.value for h in app.subheader]
    # Block 1 is conditional by design: this workbook holds no total rows, so it
    # is not shown at all rather than shown empty.
    assert any(h.startswith("2 ·") for h in headings)  # intercompany
    assert any(h.startswith("3 ·") for h in headings)  # suppliers
    assert any(h.startswith("4 ·") for h in headings)  # currencies
    # One confirmation for all of it.
    assert [b.label for b in app.button] == ["Confirm and continue"]


def test_the_total_rows_block_appears_when_there_are_total_rows(run_root, defective_run):
    from agents.spend_addressability import AddressabilityProposal
    from agents.supplier_matching import SupplierMatchProposal
    from classification.spend_classification import run_spend_classification
    from fx.currency import run_currency
    from fx.ecb import parse_ecb_csv
    from profiling.data_profiling import confirm_profiling, run_profiling
    from suppliers.normalization import run_supplier_normalization
    from transform.rule_engine import run_rule_engine

    run_profiling(defective_run)
    confirm_profiling(defective_run)
    run_rule_engine(defective_run)
    run_currency(defective_run, parse_ecb_csv("Date,PLN,\n2024-01-12,4.00,\n"))
    run_supplier_normalization(
        defective_run, client=FakeClient(SupplierMatchProposal(verdicts=[]))
    )
    run_spend_classification(
        defective_run, client=FakeClient(AddressabilityProposal(verdicts=[]))
    )

    app = _page("review", run_id=defective_run)

    assert not app.exception
    assert any(h.value.startswith("1 · Total rows") for h in app.subheader)


def test_report_shows_the_chain_from_booked_to_negotiable(run_root, portfolio_xlsx):
    from classification.spend_classification import confirm_classification
    from suppliers.normalization import confirm_suppliers

    run_id = _analysed_run(portfolio_xlsx)
    confirm_suppliers(run_id)
    confirm_classification(run_id)

    app = _page("report", run_id=run_id)

    assert not app.exception
    assert [m.label for m in app.metric][:3] == [
        "Net spend (EUR)",
        "Third party (EUR)",
        "Addressable (EUR)",
    ]
    chain = app.dataframe[0].value
    assert list(chain["Step"]) == [
        "Gross spend",
        "Credit notes",
        "Net spend",
        "Intercompany",
        "Third party spend",
        "Not addressable",
        "Addressable spend",
    ]


def test_lever_page_is_empty_before_levers_are_identified(run_root):
    app = _page("levers")

    assert not app.exception
    assert app.title[0].value == "Procurement Levers"
    assert "No levers yet" in app.info[0].value


def test_lever_page_shows_the_calculation_openly(run_root, portfolio_xlsx):
    from agents.lever_reasoning import LeverReasoningProposal
    from classification.spend_classification import confirm_classification
    from levers.engine import run_levers
    from suppliers.normalization import confirm_suppliers

    run_id = _analysed_run(portfolio_xlsx)
    confirm_suppliers(run_id)
    confirm_classification(run_id)
    run_levers(
        run_id,
        client=FakeClient(
            LeverReasoningProposal(
                levers=[], priority_rationale="", recommended_order=[], order_reason=""
            )
        ),
    )

    app = _page("levers", run_id=run_id)

    assert not app.exception
    assert [m.label for m in app.metric] == [
        "Potential — low",
        "Potential — base",
        "Potential — high",
    ]
    # The rates are an assumption and must be labelled as one, not buried.
    assert any("assumptions, not findings" in w.value for w in app.warning)

    priority = app.dataframe[0].value
    assert list(priority.columns) == [
        "#", "Lever", "Spend it applies to", "Potential (base)", "Range", "Effort", "Confidence",
    ]


def test_summary_page_is_empty_before_it_is_built(run_root):
    app = _page("summary")

    assert not app.exception
    assert app.title[0].value == "Executive Summary"
    assert "No summary yet" in app.info[0].value


def test_summary_page_shows_six_tabs(run_root, lever_run):
    from agents.sme_questions import SmeQuestion, SmeQuestionProposal
    from analysis.summary import build_summary

    build_summary(
        lever_run,
        client=FakeClient(
            SmeQuestionProposal(
                questions=[
                    SmeQuestion(
                        question="Is the missing purchase order a policy choice?",
                        rationale="Most bookings carry none.",
                        addressee="procurement",
                        unlocks="Whether maverick spend is a gap or the norm.",
                    )
                ]
            )
        ),
    )

    app = _page("summary", run_id=lever_run)

    assert not app.exception
    labels = [tab.label for tab in app.tabs]
    assert labels == [
        "Overview",
        "Top Levers",
        "All Levers",
        "Visuals",
        "Open Questions",
        "Ask the Analysis",
    ]
    # The assumption caveat belongs on the summary too, not only on the detail page:
    # quietly on the overview, prominently above the top levers.
    assert any("assumptions, not findings" in c.value for c in app.caption)
    assert any("assumptions, not findings" in w.value for w in app.warning)
