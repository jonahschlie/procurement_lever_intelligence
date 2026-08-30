import pandas as pd
import pytest

from agents.spend_addressability import AddressabilityProposal, CostTypeVerdict
from classification.spend_classification import (
    confirm_classification,
    load_confirmed,
    run_spend_classification,
)
from core.table import load_table, write_table
from profiling.data_profiling import confirm_profiling, run_profiling
from tests.conftest import FakeClient
from transform.rule_engine import run_rule_engine


@pytest.fixture
def ruled(defective_run):
    run_profiling(defective_run)
    confirm_profiling(defective_run)
    run_rule_engine(defective_run)
    return defective_run


def _verdicts(*entries):
    return AddressabilityProposal(
        verdicts=[
            CostTypeVerdict(cost_type=c, addressable=a, confidence=0.9, comment="reason")
            for c, a in entries
        ]
    )


def test_cost_types_are_read_from_the_ledger_text(ruled):
    artifact = run_spend_classification(
        ruled, client=FakeClient(_verdicts(("Freight costs", True)))
    )

    assert artifact.source_column == "gl_description"
    labels = {c.cost_type for c in artifact.cost_types}
    assert "Freight costs" in labels and "Consulting" in labels
    # Ranked by spend, so the reviewer meets the material ones first.
    assert artifact.cost_types[0].spend >= artifact.cost_types[-1].spend


def test_an_unjudged_cost_type_stays_addressable(ruled):
    # Excluding spend nobody judged would remove it from the analysis unnoticed.
    artifact = run_spend_classification(ruled, client=FakeClient(_verdicts()))

    assert all(c.addressable for c in artifact.cost_types)
    assert all("no verdict" in c.comment for c in artifact.cost_types)


def test_the_agents_verdict_becomes_a_row_flag(ruled):
    run_spend_classification(
        ruled, client=FakeClient(_verdicts(("Consulting", False), ("Freight costs", True)))
    )

    confirm_classification(ruled)

    rows = load_table(ruled).set_index("source_row")
    assert rows.loc["3", "flag_non_addressable"]  # Consulting
    assert not rows.loc["2", "flag_non_addressable"]  # Freight costs


def test_the_user_overrides_the_agent(ruled):
    run_spend_classification(ruled, client=FakeClient(_verdicts(("Consulting", False))))

    confirmed = confirm_classification(ruled, {"Consulting": True})

    consulting = next(c for c in confirmed.cost_types if c.cost_type == "Consulting")
    assert consulting.addressable
    assert consulting.decided_by == "user"
    assert not load_table(ruled).set_index("source_row").loc["3", "flag_non_addressable"]
    assert load_confirmed(ruled).cost_types == confirmed.cost_types


def test_no_cost_type_column_leaves_everything_addressable(ruled):
    table = load_table(ruled)
    table["gl_description"] = ""
    table["category"] = ""
    write_table(ruled, table, "rule_engine")

    artifact = run_spend_classification(ruled)  # no client: reaching the agent would raise

    assert artifact.source_column == ""
    assert artifact.cost_types == []


def test_the_row_count_never_changes(ruled):
    run_spend_classification(ruled, client=FakeClient(_verdicts(("Consulting", False))))

    confirm_classification(ruled)

    assert len(load_table(ruled)) == 12
