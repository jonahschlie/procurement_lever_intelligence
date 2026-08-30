from core.run import load_run, run_path
from core.table import load_table
from profiling.data_profiling import (
    confirm_profiling,
    has_confirmed,
    load_confirmed,
    load_report,
    run_profiling,
)


def _by_check(report):
    return {finding.check: finding for finding in report.findings}


def test_profiling_leaves_the_table_alone(defective_run):
    before = load_table(defective_run)

    run_profiling(defective_run)

    assert load_table(defective_run).equals(before)


def test_missing_values_are_reported_with_severity(defective_run):
    findings = _by_check(run_profiling(defective_run))

    supplier = findings["Missing Supplier"]
    assert supplier.affected_rows == 2  # one blank, one aggregate row
    assert supplier.severity == "high"  # required field, above the threshold
    # An optional field that is entirely absent is worth saying out loud;
    # one that is merely patchy is not.
    assert findings["Missing Supplier ID"].severity == "medium"
    assert findings["Missing Purchase Order"].severity == "low"


def test_duplicates_are_separated_by_kind(defective_run):
    findings = _by_check(run_profiling(defective_run))

    # INV1 twice and INV2 twice -> four rows carry a repeated document number.
    assert findings["Duplicate document numbers"].affected_rows == 4
    # Only the INV1 pair is identical in every key field.
    assert findings["Duplicate transactions"].affected_rows == 2


def test_date_problems_are_found(defective_run):
    findings = _by_check(run_profiling(defective_run))

    assert findings["Future posting dates"].affected_rows == 1
    assert findings["Posting date before document date"].affected_rows == 1


def test_negative_amounts_are_information_not_an_error(defective_run):
    finding = _by_check(run_profiling(defective_run))["Negative amounts"]

    assert finding.affected_rows == 1
    assert finding.severity == "info"


def test_both_kinds_of_aggregate_row_are_found(defective_run):
    report = run_profiling(defective_run)

    candidates = {candidate.source_row: candidate for candidate in report.aggregate_candidates}
    assert set(candidates) == {"12", "13"}
    # One is recognisable by its label, the other only by its shape.
    assert any("marker" in reason for reason in candidates["12"].reasons)
    assert candidates["13"].reasons == ["no posting date, document number or GL account"]
    assert all(candidate.exclude for candidate in report.aggregate_candidates)


def test_the_overstatement_is_quantified(defective_run):
    finding = _by_check(run_profiling(defective_run))["Embedded aggregate rows"]

    assert finding.severity == "high"
    assert finding.affected_rows == 2
    assert "overstates spend" in finding.detail


def test_reconciliation_compares_detail_against_stated_subtotals(defective_run):
    report = run_profiling(defective_run)

    entries = {entry.company: entry for entry in report.reconciliation}
    # Company A detail: 1000 + 2000 + 500 - 300 + 1000 + 750 = 4950, stated 3950.
    assert entries["A"].detail_total == 4950.0
    assert entries["A"].stated_total == 3950.0
    assert entries["A"].difference == 1000.0
    assert "Detail does not match stated subtotals" in _by_check(report)


def test_category_analysis_stays_on_when_the_category_says_something_else(defective_run):
    report = run_profiling(defective_run)

    # Only one row repeats the GL text, well below the threshold.
    assert report.category_analysis_enabled is True
    assert "differs from the GL description" in report.category_decision


def test_category_analysis_is_switched_off_when_it_only_repeats_the_gl_text(
    run_root, defective_run
):
    from core.table import load_table, write_table

    table = load_table(defective_run)
    table["category"] = table["gl_description"].str.upper()
    write_table(defective_run, table, "canonical_table")

    report = run_profiling(defective_run)

    assert report.category_analysis_enabled is False
    assert "accounting classification" in report.category_decision


def test_value_formats_are_recorded(defective_run):
    report = run_profiling(defective_run)

    assert report.value_formats["amount_local"].startswith("decimal '.'")
    assert report.value_formats["posting_date"] == "%Y-%m-%d"


def test_confirmation_records_the_users_decisions(defective_run):
    report = run_profiling(defective_run)
    keep = report.aggregate_candidates[0].position

    confirmed = confirm_profiling(defective_run, excluded={keep}, category_enabled=False)

    assert has_confirmed(defective_run)
    assert [c.exclude for c in confirmed.aggregate_candidates] == [True, False]
    assert confirmed.category_analysis_enabled is False
    assert confirmed.category_decision.startswith("Set by the user")
    assert load_confirmed(defective_run) == confirmed


def test_step_and_log_are_recorded(defective_run):
    run_profiling(defective_run)
    confirm_profiling(defective_run)

    # The fixture writes the table directly, so profiling is the first recorded step.
    assert [s.step for s in load_run(defective_run).steps] == ["profiling"]
    log = (run_path(defective_run) / "logs" / "run.log").read_text(encoding="utf-8")
    assert "profiling complete: " in log
    assert "2 aggregate row(s) to exclude" in log
