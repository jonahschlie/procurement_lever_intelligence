from agents.workbook_triage import ProposedRole, WorkbookTriageProposal
from core.run import create_run, load_run, run_path, step_path
from ingestion.storage import StagedUpload, store_files
from tests.conftest import FakeClient
from triage.workbook_triage import (
    confirm_triage,
    load_confirmed_triage,
    load_datasets,
    load_triage,
    needs_review,
    reconcile,
    run_workbook_triage,
)

SPEND = "3. Spend Data"
MASTER = "4. Supplier Master"
FX = "5. FX"


def _proposal(*entries):
    return WorkbookTriageProposal(
        sheets=[
            ProposedRole(sheet=sheet, role=role, confidence=confidence, comment=comment)
            for sheet, role, confidence, comment in entries
        ]
    )


def _sensible():
    return _proposal(
        (SPEND, "transactions", 0.96, "One booking per row with date, amount and supplier."),
        (MASTER, "supplier_master", 0.9, "Suppliers with ids and countries, no amounts."),
        (FX, "fx_rates", 0.93, "Currency codes paired with rates."),
    )


def _triage(portfolio_xlsx, proposal=None):  # run_root comes in via the test
    run_id = create_run().run_id
    store_files(run_id, [StagedUpload(portfolio_xlsx, "portfolio.xlsx", "Northwind")])
    artifact = run_workbook_triage(run_id, client=FakeClient(proposal or _sensible()))
    return run_id, artifact


def test_prose_sheets_are_documentation_without_asking(portfolio_xlsx):
    # The agent is told the cover letter is transactions; shape overrules it.
    _, artifact = _triage(
        portfolio_xlsx,
        _proposal(
            ("1. Brief", "transactions", 0.99, "Insisting on the cover letter."),
            (SPEND, "transactions", 0.96, "Real data."),
        ),
    )

    roles = {c.sheet: c.role for c in artifact.workbooks[0].classifications}
    assert roles["1. Brief"] == "documentation"
    assert roles["2. How to Submit"] == "documentation"
    assert roles[SPEND] == "transactions"


def test_agent_only_sees_the_candidate_sheets(portfolio_xlsx):
    run_id = create_run().run_id
    store_files(run_id, [StagedUpload(portfolio_xlsx, "portfolio.xlsx")])
    client = FakeClient(_sensible())

    run_workbook_triage(run_id, client=client)

    sent = client.responses.received["input"]
    assert SPEND in sent and FX in sent
    assert "1. Brief" not in sent


def test_roles_are_assigned_and_recorded(portfolio_xlsx):
    run_id, artifact = _triage(portfolio_xlsx)

    roles = {c.sheet: c.role for c in artifact.workbooks[0].classifications}
    assert roles == {
        "1. Brief": "documentation",
        "2. How to Submit": "documentation",
        SPEND: "transactions",
        MASTER: "supplier_master",
        FX: "fx_rates",
    }
    assert [s.step for s in load_run(run_id).steps] == ["ingestion", "workbook_triage"]
    assert load_triage(run_id) == artifact


def test_invented_sheets_and_unknown_roles_are_refused(portfolio_xlsx):
    _, artifact = _triage(
        portfolio_xlsx,
        _proposal(
            ("6. Does Not Exist", "transactions", 0.9, "Invented."),
            (SPEND, "spend_cube", 0.8, "Role outside the enum."),
        ),
    )

    entries = {c.sheet: c for c in artifact.workbooks[0].classifications}
    assert "6. Does Not Exist" not in entries
    assert entries[SPEND].role == "unknown"
    assert "unknown role" in entries[SPEND].comment
    # Table sheets the agent said nothing about are gaps, not silent drops.
    assert entries[FX].role == "unknown"
    assert "no answer" in entries[FX].comment


def test_confidence_is_clamped(portfolio_xlsx):
    _, artifact = _triage(portfolio_xlsx, _proposal((SPEND, "transactions", 4.2, "Overshot.")))

    entries = {c.sheet: c for c in artifact.workbooks[0].classifications}
    assert entries[SPEND].confidence == 1.0


def test_confirmation_builds_datasets_for_everything_but_documentation(portfolio_xlsx):
    run_id, _ = _triage(portfolio_xlsx)

    confirmed = confirm_triage(run_id)

    datasets = {d.role: d for d in confirmed.datasets}
    assert set(datasets) == {"transactions", "supplier_master", "fx_rates"}
    assert datasets["transactions"].dataset_id == "01_portfolio__3_spend_data"
    assert datasets["transactions"].row_count == 5
    assert "Name of Supplier" in datasets["transactions"].column_names
    assert datasets["transactions"].company_label == "Northwind"
    assert datasets["fx_rates"].row_count == 2
    assert load_datasets(run_id) == confirmed.datasets


def test_user_can_override_a_role(portfolio_xlsx):
    run_id, _ = _triage(portfolio_xlsx)

    confirmed = confirm_triage(run_id, {"01_portfolio.xlsx": {MASTER: "documentation"}})

    entries = {c.sheet: c for c in confirmed.workbooks[0].classifications}
    assert entries[MASTER].role == "documentation"
    assert entries[MASTER].decided_by == "user"
    assert entries[SPEND].decided_by == "ai"
    # Now excluded, so it no longer produces a dataset.
    assert {d.role for d in confirmed.datasets} == {"transactions", "fx_rates"}


def test_single_table_files_skip_the_agent_and_the_review(run_root, sap_csv):
    run_id = create_run().run_id
    store_files(run_id, [StagedUpload(sap_csv, "sap_export.csv")])

    # No client is passed: reaching the agent at all would raise.
    artifact = run_workbook_triage(run_id)

    assert artifact.workbooks[0].llm_call is None
    assert artifact.workbooks[0].classifications[0].role == "transactions"
    assert needs_review(artifact) is False


def test_multi_sheet_workbooks_need_review(portfolio_xlsx):
    _, artifact = _triage(portfolio_xlsx)

    assert needs_review(artifact) is True


def test_log_and_artifacts_are_written(portfolio_xlsx):
    run_id, _ = _triage(portfolio_xlsx)
    confirm_triage(run_id)

    step = step_path(run_id, "workbook_triage")
    assert (step / "workbook_triage.json").is_file()
    assert (step / "workbook_triage_confirmed.json").is_file()
    assert load_confirmed_triage(run_id).datasets

    log = (run_path(run_id) / "logs" / "run.log").read_text(encoding="utf-8")
    assert "5 sheet(s), 3 look like tables" in log
    assert "triage confirmed: 3 dataset(s)" in log
