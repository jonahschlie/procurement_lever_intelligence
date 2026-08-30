import json

import pandas as pd
import pytest

from agents.lever_reasoning import LeverNarrative, LeverReasoningProposal
from core.run import load_run
from core.table import load_table, write_table
from levers.engine import load_artifact, run_levers
from tests.conftest import FakeClient


def _proposal(order=None):
    return LeverReasoningProposal(
        levers=[
            LeverNarrative(
                lever_id="supplier_consolidation",
                opportunity="Two companies buy from Atlas separately.",
                next_steps=["Run a joint tender", "Agree one price list", "Name an owner"],
            )
        ],
        priority_rationale="Impact first, effort second.",
        recommended_order=order or [],
        order_reason="",
    )


def test_the_agent_never_sees_a_single_booking(lever_run):
    client = FakeClient(_proposal())

    run_levers(lever_run, client=client)

    sent = json.loads(client.responses.received["input"])
    assert set(sent) == {"levers", "companies"}
    # Aggregates only: no row identifiers anywhere in the payload.
    assert "source_row" not in client.responses.received["input"]
    for lever in sent["levers"]:
        assert "rows" not in lever


def test_narrative_lands_on_the_right_lever(lever_run):
    artifact = run_levers(lever_run, client=FakeClient(_proposal()))

    consolidation = next(l for l in artifact.levers if l.lever_id == "supplier_consolidation")
    other = next(l for l in artifact.levers if l.lever_id == "tail_spend")

    assert consolidation.opportunity.startswith("Two companies")
    assert len(consolidation.next_steps) == 3
    # A lever the agent said nothing about keeps its figures and stays silent.
    assert other.opportunity == ""
    assert other.potential_base > 0 or other.net_base == 0


def test_a_silent_agent_leaves_every_figure_intact(lever_run):
    empty = LeverReasoningProposal(
        levers=[], priority_rationale="", recommended_order=[], order_reason=""
    )

    artifact = run_levers(lever_run, client=FakeClient(empty))

    assert artifact.total_base > 0
    assert sum(l.net_base for l in artifact.levers) == pytest.approx(artifact.addressable_spend)


def test_an_invented_lever_id_in_the_order_is_dropped(lever_run):
    artifact = run_levers(
        lever_run, client=FakeClient(_proposal(order=["tail_spend", "not_a_lever"]))
    )

    assert artifact.agent_order == ["tail_spend"]


def test_membership_and_assignment_are_written_to_the_table(lever_run):
    run_levers(lever_run, client=FakeClient(_proposal()))

    table = load_table(lever_run)
    assert "lever_primary" in table.columns
    assert "lever_supplier_consolidation" in table.columns
    # Rows outside the addressable population carry no lever at all.
    outside = table[~table["include_addressable_spend"].astype(bool)]
    assert (outside["lever_primary"] == "").all()
    # Every addressable row is credited exactly once.
    inside = table[table["include_addressable_spend"].astype(bool)]
    assert (inside["lever_primary"] != "").all()


def test_the_step_is_recorded(lever_run):
    run_levers(lever_run, client=FakeClient(_proposal()))

    assert "levers" in [s.step for s in load_run(lever_run).steps]
    assert load_artifact(lever_run).llm_call is not None
