import pytest

from agents.schema_mapping import ProposedMapping, SchemaMappingProposal
from core.canonical import CANONICAL_FIELDS
from core.run import create_run, load_run, step_path
from ingestion.storage import StagedUpload, store_files
from triage.workbook_triage import confirm_triage, run_workbook_triage
from mapping.schema_mapping import (
    confirm_mapping,
    has_mapping,
    load_artifact,
    load_confirmed,
    reconcile,
    run_schema_mapping,
)
from tests.conftest import FakeClient

SAP_COLUMNS = [
    "Vendor",
    "Vendor ID",
    "Amount LC",
    "Currency",
    "Posting Date",
    "Document Number",
    "Account Description",
    "Company Code",
]


def _proposal(*entries):
    return SchemaMappingProposal(
        mappings=[
            ProposedMapping(
                canonical_field=field,
                source_column=column,
                confidence=confidence,
                comment=comment,
            )
            for field, column, confidence, comment in entries
        ]
    )


def _sensible_proposal():
    return _proposal(
        ("supplier", "Vendor", 0.95, "Values are company names."),
        ("supplier_id", "Vendor ID", 0.9, "Zero-padded code."),
        ("amount_local", "Amount LC", 0.92, "Pairs with the currency column."),
        ("currency", "Currency", 0.99, "ISO codes."),
        ("posting_date", "Posting Date", 0.94, "Ledger date."),
        ("invoice_number", "Document Number", 0.8, "Document identifier."),
        ("gl_description", "Account Description", 0.85, "Accounting wording."),
        ("company", "Company Code", 0.88, "Entity code."),
    )


def _prepare(run_id, *uploads):
    """Ingest and triage, so schema mapping has datasets to work on."""
    store_files(run_id, list(uploads))
    run_workbook_triage(run_id)
    confirm_triage(run_id)


def _run_with(run_root, sap_csv, proposal):
    run_id = create_run().run_id
    _prepare(run_id, StagedUpload(sap_csv, "sap_export.csv", "Alpha GmbH"))
    artifact = run_schema_mapping(run_id, client=FakeClient(proposal))
    return run_id, artifact


# --- reconciliation: the deterministic gate on what the agent returned ---


def test_every_canonical_field_appears_exactly_once():
    mappings = reconcile(_sensible_proposal().mappings, SAP_COLUMNS)

    assert [m.canonical_field for m in mappings] == [f.key for f in CANONICAL_FIELDS]


def test_unanswered_fields_become_explicit_gaps():
    mappings = {m.canonical_field: m for m in reconcile(_sensible_proposal().mappings, SAP_COLUMNS)}

    cost_center = mappings["cost_center"]
    assert cost_center.source_column is None
    assert cost_center.confidence == 0.0
    assert "no answer" in cost_center.comment


def test_a_column_the_file_does_not_have_is_refused():
    mappings = {
        m.canonical_field: m
        for m in reconcile(
            _proposal(("supplier", "Lieferant", 0.99, "Confident but invented.")).mappings,
            SAP_COLUMNS,
        )
    }

    supplier = mappings["supplier"]
    assert supplier.source_column is None
    assert supplier.confidence == 0.0
    assert "not a column in this file" in supplier.comment


def test_unknown_canonical_fields_are_dropped():
    mappings = reconcile(
        _proposal(("vat_rate", "Currency", 0.8, "Not part of the schema.")).mappings,
        SAP_COLUMNS,
    )

    assert "vat_rate" not in {m.canonical_field for m in mappings}
    assert all(m.source_column is None for m in mappings)


def test_a_column_claimed_twice_is_flagged_on_the_second_claim():
    mappings = {
        m.canonical_field: m
        for m in reconcile(
            _proposal(
                ("gl_description", "Account Description", 0.9, "Accounting wording."),
                ("category", "Account Description", 0.6, "Could be a category."),
            ).mappings,
            SAP_COLUMNS,
        )
    }

    assert mappings["gl_description"].source_column == "Account Description"
    assert mappings["category"].source_column == "Account Description"
    assert "also proposed for 'gl_description'" in mappings["category"].comment


@pytest.mark.parametrize("given,expected", [(1.7, 1.0), (-0.5, 0.0), (0.42, 0.42)])
def test_confidence_is_clamped(given, expected):
    mappings = {
        m.canonical_field: m
        for m in reconcile(_proposal(("supplier", "Vendor", given, "x")).mappings, SAP_COLUMNS)
    }

    assert mappings["supplier"].confidence == expected


# --- the step: artifacts, run trail, confirmation ---


def test_writes_the_artifact_and_records_the_step(run_root, sap_csv):
    run_id, artifact = _run_with(run_root, sap_csv, _sensible_proposal())

    assert has_mapping(run_id)
    assert (step_path(run_id, "schema_mapping") / "schema_mapping.json").is_file()
    assert [s.step for s in load_run(run_id).steps] == [
        "ingestion",
        "workbook_triage",
        "schema_mapping",
    ]
    assert load_artifact(run_id) == artifact


def test_artifact_records_what_was_sent_and_what_it_cost(run_root, sap_csv):
    _, artifact = _run_with(run_root, sap_csv, _sensible_proposal())
    dataset = artifact.datasets[0]

    assert dataset.original_filename == "sap_export.csv"
    assert dataset.dataset_id == "01_sap_export"
    assert [p.name for p in dataset.column_profiles] == SAP_COLUMNS
    assert dataset.llm_call.model == "gpt-5-mini-test"
    assert dataset.llm_call.input_tokens == 1234


def test_log_reports_the_mapping(run_root, sap_csv):
    from core.run import run_path

    run_id, _ = _run_with(run_root, sap_csv, _sensible_proposal())

    log = (run_path(run_id) / "logs" / "run.log").read_text(encoding="utf-8")
    assert "8 column(s) sent to the agent" in log
    assert "8 of 15 canonical fields matched" in log
    assert log.count("schema mapping complete: 1 dataset(s)") == 1


def test_confirmation_marks_only_what_the_user_changed(run_root, sap_csv):
    run_id, artifact = _run_with(run_root, sap_csv, _sensible_proposal())
    dataset = artifact.datasets[0]
    selections = {m.canonical_field: m.source_column for m in dataset.mappings}
    selections["category"] = "Account Description"

    confirmed = confirm_mapping(run_id, {dataset.dataset_id: selections})

    mappings = {m.canonical_field: m for m in confirmed.datasets[0].mappings}
    assert mappings["category"].source_column == "Account Description"
    assert mappings["category"].decided_by == "user"
    assert mappings["category"].confidence == 1.0
    assert mappings["supplier"].decided_by == "ai"
    assert load_confirmed(run_id) == confirmed


def test_confirming_a_second_dataset_keeps_the_first_one(run_root, sap_csv, oracle_csv):
    run_id = create_run().run_id
    _prepare(
        run_id,
        StagedUpload(sap_csv, "sap_export.csv"),
        StagedUpload(oracle_csv, "oracle_export.csv"),
    )
    artifact = run_schema_mapping(run_id, client=FakeClient(_sensible_proposal()))
    first, second = artifact.datasets

    confirm_mapping(run_id, {first.dataset_id: {"category": "Account Description"}})
    confirm_mapping(run_id, {second.dataset_id: {"company": "Operating Unit"}})

    confirmed = {d.dataset_id: d for d in load_confirmed(run_id).datasets}
    first_category = next(
        m for m in confirmed[first.dataset_id].mappings if m.canonical_field == "category"
    )
    second_company = next(
        m for m in confirmed[second.dataset_id].mappings if m.canonical_field == "company"
    )
    assert first_category.decided_by == "user"
    assert second_company.decided_by == "user"
