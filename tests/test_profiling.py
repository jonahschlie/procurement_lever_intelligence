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


def test_a_supplier_merely_containing_total_is_not_a_candidate(run_root, defective_run):
    from core.table import load_table, write_table

    table = load_table(defective_run)
    # A fully identified booking from a supplier whose name contains TOTAL.
    table.loc[0, "supplier"] = "TotalEnergies SE"
    write_table(defective_run, table, "canonical_table")

    report = run_profiling(defective_run)

    assert "2" not in {candidate.source_row for candidate in report.aggregate_candidates}


def test_a_marker_on_a_full_booking_is_shown_but_not_preticked(run_root, defective_run):
    from core.table import load_table, write_table

    table = load_table(defective_run)
    # Standalone word TOTAL, but the row keeps date, document number and account.
    table.loc[0, "supplier"] = "TOTAL"
    write_table(defective_run, table, "canonical_table")

    report = run_profiling(defective_run)

    candidate = next(c for c in report.aggregate_candidates if c.source_row == "2")
    assert candidate.exclude is False
    # The structurally empty ones stay preticked.
    assert all(c.exclude for c in report.aggregate_candidates if c.source_row in ("12", "13"))


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

    # One GL description carries two different categories, so it does not predict them.
    assert report.category_analysis_enabled is True
    assert "carries its own meaning" in report.category_decision


def test_category_analysis_is_switched_off_when_it_only_renames_the_gl_text(
    run_root, defective_run
):
    from core.table import load_table, write_table

    table = load_table(defective_run)
    table["category"] = table["gl_description"].str.upper()
    write_table(defective_run, table, "canonical_table")

    report = run_profiling(defective_run)

    assert report.category_analysis_enabled is False
    assert "accounting classification" in report.category_decision


def test_a_renamed_category_is_caught_even_though_the_strings_differ(run_root, defective_run):
    """The case a string comparison misses: 'ESS - SUBCONTRACTS' becomes 'Subcontracts'."""
    from core.table import load_table, write_table

    table = load_table(defective_run)
    renamed = {
        "Freight costs": "Logistics Services",
        "Consulting": "Advisory",
        "Office supplies": "Facility Management",
        "Miscellaneous": "Other",
        "Other expenses": "Other",
    }
    table["category"] = table["gl_description"].map(lambda gl: renamed.get(gl, ""))
    write_table(defective_run, table, "canonical_table")

    report = run_profiling(defective_run)

    # Not one category string equals its GL description, yet the column adds nothing.
    assert report.category_analysis_enabled is False
    assert "100.0%" in report.category_decision


def test_supplier_names_in_the_category_column_are_reported(run_root, defective_run):
    from core.table import load_table, write_table

    table = load_table(defective_run)
    table.loc[0, "category"] = "Atlas Freight"  # a supplier, not a category
    write_table(defective_run, table, "canonical_table")

    finding = _by_check(run_profiling(defective_run))["Supplier names in the category column"]

    assert finding.affected_rows == 1
    assert finding.severity == "medium"


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


# --- the sum signal ---------------------------------------------------------
#
# A subtotal that kept its posting date, document number and GL account passes
# both the marker and the shape test. What still gives it away is arithmetic.


def _block(amounts, **overrides):
    """A block of fully identified bookings, one row per amount."""
    import pandas as pd

    frame = pd.DataFrame(
        {
            "dataset_id": ["ds1"] * len(amounts),
            "source_row": [str(i + 2) for i in range(len(amounts))],
            "company": ["1001"] * len(amounts),
            "company_name": ["Helios Iberia"] * len(amounts),
            "supplier": [f"Supplier {i}" for i in range(len(amounts))],
            "posting_date": ["2025-01-15"] * len(amounts),
            "invoice_number": [f"INV-{i}" for i in range(len(amounts))],
            "gl_account": ["6000"] * len(amounts),
            "gl_description": ["Consulting"] * len(amounts),
            "category": [""] * len(amounts),
            "amount_local": [str(a) for a in amounts],
            "amount_group": [""] * len(amounts),
        }
    )
    for column, values in overrides.items():
        frame[column] = values
    return frame, pd.Series(amounts, dtype=float)


def test_a_subtotal_that_kept_its_identifiers_is_caught_by_arithmetic():
    from profiling.data_profiling import _aggregate_candidates

    # Five bookings and, below them, their total -- fully identified throughout.
    table, amount = _block([100.0, 250.0, 300.0, 175.0, 225.0, 1050.0])

    candidates = _aggregate_candidates(table, amount)

    assert [c.source_row for c in candidates] == ["7"]
    assert "matches the sum of the other 5 rows" in candidates[0].reasons[0]


def test_the_sum_signal_proposes_but_never_preticks():
    from profiling.data_profiling import _aggregate_candidates

    table, amount = _block([100.0, 250.0, 300.0, 175.0, 225.0, 1050.0])

    # Nothing about it is certain enough to exclude spend without being asked.
    assert _aggregate_candidates(table, amount)[0].exclude is False


def test_rounding_in_the_source_still_matches():
    from profiling.data_profiling import _aggregate_candidates

    table, amount = _block([100.0, 250.0, 300.0, 175.0, 225.0, 1052.0])

    assert len(_aggregate_candidates(table, amount)) == 1


def test_a_block_too_small_to_be_summarised_nominates_nobody():
    from profiling.data_profiling import _aggregate_candidates

    # Three bookings and their total: too few to tell a subtotal from a coincidence.
    table, amount = _block([100.0, 250.0, 300.0, 650.0])

    assert _aggregate_candidates(table, amount) == []


def test_ordinary_bookings_of_similar_size_are_left_alone():
    from profiling.data_profiling import _aggregate_candidates

    table, amount = _block([200.0, 200.0, 200.0, 200.0, 200.0, 200.0])

    assert _aggregate_candidates(table, amount) == []


def test_a_row_already_recognised_is_not_counted_into_the_sums():
    from profiling.data_profiling import _aggregate_candidates

    # A grand total sits in the same block. Left in the sums it would hide the
    # subtotal; excluded, both are found -- the total by its marker, the
    # subtotal by arithmetic.
    table, amount = _block([100.0, 250.0, 300.0, 175.0, 225.0, 1050.0, 1050.0])
    table.loc[6, "supplier"] = "*** GRAND TOTAL ***"

    rows = {c.source_row: c.reasons for c in _aggregate_candidates(table, amount)}

    assert set(rows) == {"7", "8"}
    assert "matches the sum" in rows["7"][0]
    assert "marker" in rows["8"][0]
