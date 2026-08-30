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
