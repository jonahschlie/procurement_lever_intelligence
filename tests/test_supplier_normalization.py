import pytest

from agents.supplier_matching import PairVerdict, SupplierMatchProposal
from core.table import load_table, load_table_meta, write_table
from suppliers import normalization
from suppliers.normalization import (
    confirm_suppliers,
    load_confirmed,
    run_supplier_normalization,
)
from tests.conftest import FakeClient


@pytest.fixture
def name_run(defective_run):
    """The defective table, with supplier variants worth matching."""
    table = load_table(defective_run)
    variants = {
        "2": "Atlas Freight & Logistics",
        "5": "Atlas Frght & Log.",
        "6": "ATLAS FREIGHT & LOGISTICS",
        "3": "Sopra Steria",
        "7": "Sopra Steria SA",
    }
    for source_row, name in variants.items():
        table.loc[table["source_row"] == source_row, "supplier"] = name
    write_table(defective_run, table, "canonical_table")
    return defective_run


def _same(comment="Abbreviation of the same firm."):
    return SupplierMatchProposal(
        verdicts=[PairVerdict(pair_id=0, same=True, confidence=0.9, comment=comment)]
    )


def test_cleanup_identical_names_merge_without_the_agent(name_run):
    client = FakeClient(_same())

    artifact = run_supplier_normalization(name_run, client=client)

    groups = {group.canonical_name: group for group in artifact.groups}
    # Sopra Steria / Sopra Steria SA differ only in suffix: deterministic, preapproved.
    sopra = next(g for g in artifact.groups if "Sopra Steria" in g.members)
    assert sopra.source == "deterministic"
    assert sopra.approved
    assert set(sopra.members) == {"Sopra Steria", "Sopra Steria SA"}


def test_the_agent_settles_the_grey_zone(name_run):
    artifact = run_supplier_normalization(name_run, client=FakeClient(_same()))

    atlas = next(g for g in artifact.groups if "Atlas Frght & Log." in g.members)
    assert set(atlas.members) == {
        "ATLAS FREIGHT & LOGISTICS",
        "Atlas Freight & Logistics",
        "Atlas Frght & Log.",
    }
    assert atlas.source == "ai"
    assert atlas.approved
    assert artifact.llm_call is not None


def test_an_unsure_agent_leaves_the_group_unapproved(name_run):
    unsure = SupplierMatchProposal(
        verdicts=[
            PairVerdict(pair_id=0, same=True, confidence=0.55, comment="Could be either.")
        ]
    )

    artifact = run_supplier_normalization(name_run, client=FakeClient(unsure))

    atlas = next(g for g in artifact.groups if "Atlas Frght & Log." in g.members)
    assert atlas.source == "ai_unsure"
    assert not atlas.approved


def test_a_no_verdict_keeps_the_names_apart(name_run):
    no = SupplierMatchProposal(
        verdicts=[
            PairVerdict(pair_id=0, same=False, confidence=0.85, comment="Two legal entities.")
        ]
    )

    artifact = run_supplier_normalization(name_run, client=FakeClient(no))

    assert not any(
        "Atlas Frght & Log." in g.members and len(g.members) > 2 for g in artifact.groups
    )
    assert any(pair.comment == "Two legal entities." for pair in artifact.rejected)


def test_master_names_lend_their_id_and_country(name_run, monkeypatch):
    monkeypatch.setattr(
        normalization,
        "_load_master",
        lambda run_id: {"Atlas Freight & Logistics": {"id": "SUP-2010", "country": "SE"}},
    )

    artifact = run_supplier_normalization(name_run, client=FakeClient(_same()))

    atlas = next(g for g in artifact.groups if "Atlas Frght & Log." in g.members)
    assert atlas.canonical_id == "SUP-2010"
    assert atlas.country == "SE"
    assert atlas.canonical_name == "Atlas Freight & Logistics"


def test_confirmation_writes_columns_and_leaves_the_raw_name_alone(name_run):
    run_supplier_normalization(name_run, client=FakeClient(_same()))

    confirm_suppliers(name_run)

    table = load_table(name_run).set_index("source_row")
    assert table.loc["5", "supplier"] == "Atlas Frght & Log."  # untouched
    assert table.loc["5", "supplier_normalized"] == table.loc["2", "supplier_normalized"]
    assert table.loc["5", "supplier_canonical_id"] == table.loc["2", "supplier_canonical_id"]
    # Rows without a supplier stay empty rather than inheriting anything.
    assert table.loc["4", "supplier_normalized"] == ""
    meta = load_table_meta(name_run)
    assert "supplier_canonical_id" in meta.revisions[-1].columns_added


def test_rejecting_a_group_splits_it_back_into_singletons(name_run):
    artifact = run_supplier_normalization(name_run, client=FakeClient(_same()))
    atlas = next(g for g in artifact.groups if "Atlas Frght & Log." in g.members)

    confirmed = confirm_suppliers(name_run, approvals={atlas.group_id: False})

    table = load_table(name_run).set_index("source_row")
    assert table.loc["5", "supplier_canonical_id"] != table.loc["2", "supplier_canonical_id"]
    assert table.loc["5", "supplier_normalized"] == "Atlas Frght & Log."
    group = next(g for g in confirmed.groups if g.group_id == atlas.group_id)
    assert group.source == "user"
    assert load_confirmed(name_run).groups == confirmed.groups


def test_the_user_can_rename_the_canonical_supplier(name_run):
    artifact = run_supplier_normalization(name_run, client=FakeClient(_same()))
    atlas = next(g for g in artifact.groups if "Atlas Frght & Log." in g.members)

    confirm_suppliers(name_run, names={atlas.group_id: "Atlas Freight & Logistics AB"})

    table = load_table(name_run).set_index("source_row")
    assert table.loc["2", "supplier_normalized"] == "Atlas Freight & Logistics AB"


def _master_with_contracts(run_id):
    return {
        "Atlas Freight & Logistics": {"id": "SUP-1", "country": "SE", "contract": True},
        "Sopra Steria": {"id": "SUP-2", "country": "FR", "contract": False},
    }


def test_contract_status_comes_from_the_master(name_run, monkeypatch):
    monkeypatch.setattr(normalization, "_load_master", _master_with_contracts)

    artifact = run_supplier_normalization(name_run, client=FakeClient(_same()))

    groups = {g.canonical_id: g for g in artifact.groups}
    assert groups["SUP-1"].contract_on_file is True
    assert groups["SUP-2"].contract_on_file is False
    # A supplier the master does not list is unknown, not "no contract".
    others = [g for g in artifact.groups if g.master_id is None]
    assert others and all(g.contract_on_file is None for g in others)


def test_contract_status_lands_in_the_table_three_valued(name_run, monkeypatch):
    monkeypatch.setattr(normalization, "_load_master", _master_with_contracts)
    run_supplier_normalization(name_run, client=FakeClient(_same()))

    confirm_suppliers(name_run)

    rows = load_table(name_run).set_index("source_row")
    assert rows.loc["2", "supplier_contract_status"] == "yes"  # Atlas cluster
    assert rows.loc["3", "supplier_contract_status"] == "no"  # Sopra cluster
    # Row 4 has no supplier at all, so it makes no claim either way.
    assert rows.loc["4", "supplier_contract_status"] == ""


def test_a_blank_flag_means_no_contract_a_missing_column_means_unknown(name_run, monkeypatch):
    monkeypatch.setattr(
        normalization,
        "_load_master",
        lambda run_id: {"Sopra Steria": {"id": "SUP-9", "country": "", "contract": None}},
    )

    artifact = run_supplier_normalization(name_run, client=FakeClient(_same()))

    sopra = next(g for g in artifact.groups if g.master_id == "SUP-9")
    assert sopra.contract_on_file is None


def test_a_rejected_group_does_not_inherit_a_contract_status(name_run, monkeypatch):
    monkeypatch.setattr(normalization, "_load_master", _master_with_contracts)
    artifact = run_supplier_normalization(name_run, client=FakeClient(_same()))
    atlas = next(g for g in artifact.groups if g.canonical_id == "SUP-1")

    confirm_suppliers(name_run, approvals={atlas.group_id: False})

    rows = load_table(name_run).set_index("source_row")
    assert rows.loc["2", "supplier_contract_status"] == "unknown"


# --- editing the groups by hand ---------------------------------------------
#
# Approving and renaming cannot move a name between groups, invent a group or
# split one. An explicit name-to-group map can, and it has to leave the path
# without one untouched.


def test_confirming_without_a_map_is_exactly_what_it_was(name_run):
    run_supplier_normalization(name_run, client=FakeClient(_same()))

    plain = confirm_suppliers(name_run)
    with_none = confirm_suppliers(name_run, assignments=None)

    assert plain.model_dump() == with_none.model_dump()
    assert {g.source for g in plain.groups} <= {"deterministic", "ai", "ai_unsure"}


def test_a_name_moved_by_hand_lands_in_the_other_group(name_run):
    artifact = run_supplier_normalization(name_run, client=FakeClient(_same()))
    assignments = {
        member: group.canonical_name
        for group in artifact.groups
        for member in group.members
    }
    assignments["Sopra Steria SA"] = "Atlas Freight & Logistics"

    confirmed = confirm_suppliers(name_run, assignments=assignments)

    atlas = next(g for g in confirmed.groups if g.canonical_name == "Atlas Freight & Logistics")
    assert "Sopra Steria SA" in atlas.members
    assert atlas.source == "user"
    # And the table follows the decision.
    table = load_table(name_run).set_index("source_row")
    assert table.loc["7", "supplier_normalized"] == "Atlas Freight & Logistics"


def test_a_label_nobody_proposed_becomes_a_group(name_run):
    artifact = run_supplier_normalization(name_run, client=FakeClient(_same()))
    assignments = {
        member: "Atlas Group"
        for group in artifact.groups
        for member in group.members
        if "atlas" in member.lower()
    }

    confirmed = confirm_suppliers(name_run, assignments=assignments)

    invented = next(g for g in confirmed.groups if g.canonical_name == "Atlas Group")
    assert len(invented.members) == 3
    assert invented.approved


def test_splitting_a_group_leaves_each_name_on_its_own(name_run):
    artifact = run_supplier_normalization(name_run, client=FakeClient(_same()))
    atlas = next(g for g in artifact.groups if "Atlas Frght & Log." in g.members)

    # An empty cell means "this name is its own supplier".
    confirmed = confirm_suppliers(name_run, assignments={member: "" for member in atlas.members})

    names = {g.canonical_name for g in confirmed.groups}
    assert set(atlas.members) <= names
    table = load_table(name_run).set_index("source_row")
    assert table.loc["5", "supplier_normalized"] == "Atlas Frght & Log."


def test_a_regrouped_supplier_keeps_the_master_entry_it_caught(name_run, monkeypatch):
    monkeypatch.setattr(normalization, "_load_master", _master_with_contracts)
    artifact = run_supplier_normalization(name_run, client=FakeClient(_same()))
    assignments = {
        member: "Atlas Group"
        for group in artifact.groups
        for member in group.members
        if "atlas" in member.lower()
    }

    confirmed = confirm_suppliers(name_run, assignments=assignments)

    atlas = next(g for g in confirmed.groups if g.canonical_name == "Atlas Group")
    # Country and contract status may only come from the master, so the master
    # entry survives the regrouping.
    assert atlas.master_id == "SUP-1"
    assert atlas.country == "SE"
    assert atlas.contract_on_file is True


def test_an_intercompany_mark_carries_into_the_group_built_by_hand(name_run):
    artifact = run_supplier_normalization(name_run, client=FakeClient(_same()))
    atlas = next(g for g in artifact.groups if "Atlas Frght & Log." in g.members)
    assignments = {
        member: group.canonical_name
        for group in artifact.groups
        for member in group.members
    }

    confirmed = confirm_suppliers(
        name_run, intercompany={atlas.group_id: True}, assignments=assignments
    )

    rebuilt = next(g for g in confirmed.groups if "Atlas Frght & Log." in g.members)
    assert rebuilt.is_intercompany
