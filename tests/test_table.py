import pandas as pd

from core.run import create_run, run_path
from core.table import has_table, load_table, load_table_meta, write_table


def _frame(**columns):
    return pd.DataFrame(columns, dtype=str)


def test_round_trips_through_parquet(run_root):
    run_id = create_run().run_id
    frame = _frame(supplier=["Müller Logistik GmbH", "ABC Ltd"], amount_local=["1.250,00", "0"])

    write_table(run_id, frame, "canonical_table")

    assert has_table(run_id)
    assert (run_path(run_id) / "canonical_table.parquet").is_file()
    restored = load_table(run_id)
    assert restored.equals(frame)
    # Text in, text out: nothing is reinterpreted by the storage layer either.
    assert restored.loc[0, "amount_local"] == "1.250,00"


def test_metadata_describes_the_table(run_root):
    run_id = create_run().run_id

    meta = write_table(run_id, _frame(a=["1"], b=["2"]), "canonical_table", note="built it")

    assert (meta.row_count, meta.column_names) == (1, ["a", "b"])
    assert len(meta.revisions) == 1
    assert meta.revisions[0].step == "canonical_table"
    assert meta.revisions[0].columns_added == ["a", "b"]
    assert meta.revisions[0].note == "built it"
    assert load_table_meta(run_id) == meta


def test_history_records_what_each_step_added(run_root):
    run_id = create_run().run_id
    write_table(run_id, _frame(a=["1"], b=["2"]), "canonical_table")

    meta = write_table(
        run_id, _frame(a=["1"], b=["2"], flag_missing_supplier=["false"]), "rule_engine"
    )

    assert [revision.step for revision in meta.revisions] == ["canonical_table", "rule_engine"]
    # Only the genuinely new column is reported, and the row count is unchanged.
    assert meta.revisions[1].columns_added == ["flag_missing_supplier"]
    assert meta.revisions[1].row_count == 1


def test_no_table_before_anything_is_written(run_root):
    run_id = create_run().run_id

    assert not has_table(run_id)
