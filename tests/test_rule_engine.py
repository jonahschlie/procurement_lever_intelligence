import pandas as pd
import pytest

from core.run import load_run, run_path
from core.table import load_table, load_table_meta
from profiling.data_profiling import confirm_profiling, run_profiling
from transform.rule_engine import load_report, run_rule_engine


@pytest.fixture
def applied(defective_run):
    run_profiling(defective_run)
    confirm_profiling(defective_run)
    return defective_run, run_rule_engine(defective_run)


def _flags(run_id):
    table = load_table(run_id)
    return table.set_index("source_row")


def test_no_row_is_ever_removed(applied):
    run_id, report = applied

    assert report.row_count == 12
    assert len(load_table(run_id)) == 12


def test_values_are_typed_beside_the_originals(applied):
    run_id, _ = applied
    rows = _flags(run_id)

    assert rows.loc["2", "amount_local"] == "1000.00"
    assert rows.loc["2", "amount_local_value"] == 1000.0
    assert str(rows.loc["2", "posting_date_value"].date()) == "2024-01-15"
    assert pd.isna(rows.loc["11", "amount_local_value"])


def test_each_flag_hits_the_rows_it_should(applied):
    run_id, _ = applied
    rows = _flags(run_id)

    assert list(rows.index[rows["flag_missing_supplier"]]) == ["4", "13"]
    assert list(rows.index[rows["flag_negative_amount"]]) == ["5"]
    assert list(rows.index[rows["flag_future_date"]]) == ["9"]
    assert list(rows.index[rows["flag_date_order"]]) == ["10"]
    assert list(rows.index[rows["flag_missing_currency"]]) == ["8"]
    assert list(rows.index[rows["flag_aggregate_row"]]) == ["12", "13"]
    assert set(rows.index[rows["flag_duplicate_document"]]) == {"2", "3", "6", "7"}
    assert set(rows.index[rows["flag_duplicate_transaction"]]) == {"2", "6"}


def test_spend_excludes_the_aggregate_rows_only(applied):
    run_id, report = applied
    rows = _flags(run_id)

    # 12 rows, two of them aggregates, one with no amount at all.
    assert report.excluded_rows == 3
    assert not rows.loc["12", "include_spend_analysis"]
    assert not rows.loc["11", "include_spend_analysis"]  # no amount
    assert rows.loc["2", "include_spend_analysis"]
    assert report.spend_before - report.spend_after == pytest.approx(5750.0)


def test_a_missing_currency_is_rescued_by_the_group_amount(applied):
    run_id, _ = applied
    rows = _flags(run_id)

    # Row 8 has no currency, but a group amount, so section 11 keeps it in.
    assert rows.loc["8", "flag_missing_currency"]
    assert rows.loc["8", "include_spend_analysis"]


def test_eligibility_narrows_down_per_analysis(applied):
    run_id, report = applied
    rows = _flags(run_id)

    assert not rows.loc["4", "include_supplier_analysis"]  # no supplier
    assert rows.loc["4", "include_spend_analysis"]  # but its spend still counts
    assert report.eligibility["include_spend_analysis"] == 9
    assert report.eligibility["include_supplier_analysis"] == 8


def test_a_rejected_candidate_stays_in_the_spend(defective_run):
    report = run_profiling(defective_run)
    keep = report.aggregate_candidates[0].position

    confirm_profiling(defective_run, excluded={keep})
    run_rule_engine(defective_run)

    rows = _flags(defective_run)
    # The user kept row 13, so it is neither flagged nor excluded.
    assert not rows.loc["13", "flag_aggregate_row"]
    assert rows.loc["13", "include_spend_analysis"]


def test_category_eligibility_follows_the_users_decision(defective_run):
    run_profiling(defective_run)
    confirm_profiling(defective_run, category_enabled=False)
    run_rule_engine(defective_run)

    assert not load_table(defective_run)["include_category_analysis"].any()


def test_table_history_records_the_added_columns(applied):
    run_id, _ = applied
    meta = load_table_meta(run_id)

    assert [revision.step for revision in meta.revisions] == ["canonical_table", "rule_engine"]
    added = meta.revisions[1].columns_added
    assert "flag_aggregate_row" in added
    assert "include_spend_analysis" in added
    assert "amount_local_value" in added
    # Adding columns, never rows.
    assert meta.revisions[1].row_count == meta.revisions[0].row_count


def test_step_and_log_are_recorded(applied):
    run_id, report = applied

    assert [s.step for s in load_run(run_id).steps] == ["profiling", "rule_engine"]
    log = (run_path(run_id) / "logs" / "run.log").read_text(encoding="utf-8")
    assert "rule engine complete" in log
    assert load_report(run_id) == report


def test_a_supplier_name_in_the_category_column_is_excluded_from_category_analysis(
    defective_run,
):
    from core.table import load_table, write_table

    table = load_table(defective_run)
    table.loc[0, "category"] = "Atlas Freight"
    write_table(defective_run, table, "canonical_table")

    run_profiling(defective_run)
    confirm_profiling(defective_run)
    run_rule_engine(defective_run)

    rows = _flags(defective_run)
    assert rows.loc["2", "flag_category_is_supplier"]
    assert not rows.loc["2", "include_category_analysis"]
    # The value itself is kept -- nothing is overwritten.
    assert rows.loc["2", "category"] == "Atlas Freight"
