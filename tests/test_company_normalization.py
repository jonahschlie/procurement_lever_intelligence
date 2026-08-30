"""Company normalization: one legal entity per name, however each export spells it."""

import pandas as pd
import pytest

from companies.normalization import (
    confirm_companies,
    load_confirmed,
    run_company_normalization,
)
from core.canonical import company_identity, company_key
from core.run import create_run
from core.table import load_table, write_table
from transform.canonical_table import BASE_COLUMNS


def _table(rows) -> pd.DataFrame:
    """A canonical table holding only what this stage reads."""
    records = []
    for index, (dataset, code, name) in enumerate(rows):
        record = {column: "" for column in BASE_COLUMNS}
        record.update(
            dataset_id=dataset,
            source_file=f"{dataset}.csv",
            source_row=str(index + 2),
            company=code,
            company_name=name,
            supplier=f"Supplier {index}",
            amount_local="100.00",
        )
        records.append(record)
    return pd.DataFrame(records, dtype=str)[list(BASE_COLUMNS)]


@pytest.fixture
def run_with(run_root):
    def make(rows):
        run_id = create_run().run_id
        write_table(run_id, _table(rows), "canonical_table")
        return run_id

    return make


def test_one_export_spelling_its_companies_consistently_changes_nothing(run_with):
    run_id = run_with(
        [("ds1", "1001", "Helios Iberia"), ("ds1", "1002", "Helios Polska")]
    )

    artifact = run_company_normalization(run_id)

    assert [g.canonical_name for g in artifact.groups] == ["Helios Iberia", "Helios Polska"]
    assert all(len(g.members) == 1 for g in artifact.groups)


def test_a_code_settles_the_spelling_within_one_export(run_with):
    run_id = run_with(
        [("ds1", "1001", "Helios Renewables Iberia, S.A."), ("ds1", "1001", "HELIOS IBERIA")]
    )

    groups = run_company_normalization(run_id).groups

    assert len(groups) == 1
    assert "same company code" in groups[0].comment
    assert groups[0].approved


def test_the_same_name_in_two_exports_is_one_company(run_with):
    run_id = run_with(
        [
            ("ds1", "1001", "Helios Power Polska Sp. z o.o."),
            ("ds2", "4711", "HELIOS POWER POLSKA"),
        ]
    )

    groups = run_company_normalization(run_id).groups

    assert len(groups) == 1
    assert {m.dataset_id for m in groups[0].members} == {"ds1", "ds2"}


def test_the_same_code_for_unrelated_names_is_a_collision_not_a_merge(run_with):
    # Two ERPs both numbering their entities from 1000. Merging them would count
    # two different companies as one, silently.
    run_id = run_with(
        [("ds1", "1000", "Helios Iberia"), ("ds2", "1000", "Vulcan Logistics")]
    )

    groups = run_company_normalization(run_id).groups

    assert len(groups) == 2
    assert all(g.code_collision for g in groups)
    # A collision is the one case that wants a decision rather than a glance.
    assert not any(g.approved for g in groups)


def test_confirming_writes_the_canonical_columns_beside_the_raw_ones(run_with):
    run_id = run_with(
        [("ds1", "1001", "Helios Renewables Iberia, S.A."), ("ds1", "1001", "HELIOS IBERIA")]
    )
    run_company_normalization(run_id)

    confirm_companies(run_id)

    table = load_table(run_id)
    assert table["company_normalized"].nunique() == 1
    assert table["company_canonical_id"].nunique() == 1
    # The export's own values are never overwritten.
    assert table["company_name"].tolist() == [
        "Helios Renewables Iberia, S.A.",
        "HELIOS IBERIA",
    ]


def test_a_group_the_user_rejects_falls_back_to_one_company_per_spelling(run_with):
    run_id = run_with(
        [("ds1", "1001", "Helios Renewables Iberia, S.A."), ("ds1", "1001", "HELIOS IBERIA")]
    )
    artifact = run_company_normalization(run_id)

    confirmed = confirm_companies(run_id, {artifact.groups[0].group_id: False})

    assert not confirmed.groups[0].approved
    assert load_table(run_id)["company_normalized"].nunique() == 2


def test_renaming_a_company_is_recorded_as_the_users_decision(run_with):
    run_id = run_with([("ds1", "1001", "HELIOS IBERIA")])
    group_id = run_company_normalization(run_id).groups[0].group_id

    confirmed = confirm_companies(run_id, names={group_id: "Helios Renewables Iberia"})

    assert confirmed.groups[0].canonical_name == "Helios Renewables Iberia"
    assert confirmed.groups[0].source == "user"
    assert load_confirmed(run_id).groups[0].canonical_name == "Helios Renewables Iberia"


def test_a_grand_total_row_cannot_nominate_itself_as_a_company(run_with):
    run_id = run_with([("ds1", "1001", "Helios Iberia"), ("ds1", "", "GRAND TOTAL")])
    table = load_table(run_id)
    table["flag_aggregate_row"] = [False, True]
    write_table(run_id, table, "canonical_table")

    groups = run_company_normalization(run_id).groups

    assert [g.canonical_name for g in groups] == ["Helios Iberia"]


def test_a_run_without_the_stage_groups_exactly_as_it_always_did(run_with):
    run_id = run_with([("ds1", "1001", "Helios Iberia"), ("ds1", "1002", "Helios Polska")])
    table = load_table(run_id)

    # No company_normalized column: the raw name and the raw code still answer.
    assert company_key(table).tolist() == ["Helios Iberia", "Helios Polska"]
    assert company_identity(table).tolist() == ["1001", "1002"]
