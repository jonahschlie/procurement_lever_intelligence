"""One assembly behind the screen and both exports."""

import pandas as pd
import pytest

from agents.sme_questions import SmeQuestion, SmeQuestionProposal
from analysis.report import BY_COMPANY, build_report, group_name, visual_blocks
from analysis.spend_report import spend_chain
from analysis.summary import build_summary
from core.table import load_table
from levers.engine import load_artifact
from tests.conftest import FakeClient


def _proposal():
    return SmeQuestionProposal(
        questions=[
            SmeQuestion(
                question="Is the missing purchase order reference a policy choice?",
                rationale="Most bookings carry no purchase order.",
                addressee="procurement",
                unlocks="Whether maverick spend is a compliance gap or the normal route.",
            )
        ]
    )


@pytest.fixture
def reported(lever_run):
    build_summary(lever_run, client=FakeClient(_proposal()))
    return lever_run


def test_the_document_carries_every_section_in_reading_order(reported):
    document = build_report(reported)

    assert [section.title for section in document.sections] == [
        "Overview",
        "Top Levers",
        "All Levers",
        "Visuals",
        "Open Questions",
    ]
    assert all(section.blocks for section in document.sections)


def test_the_cover_states_only_what_the_run_can_show(reported):
    cover = build_report(reported).cover

    assert cover.run_id == reported
    assert cover.rows_total == len(load_table(reported))
    assert cover.rows_analysed <= cover.rows_total
    # This run was built from a table directly, with no file submitted. The cover
    # says so rather than inventing a source.
    assert cover.sources == ()
    assert "assumptions, not findings" in cover.note


def test_the_group_is_named_by_the_data_rather_than_configured():
    table = pd.DataFrame(
        {"company_name": ["Helios Renewables Iberia", "Helios Power Polska", "Helios Comunidades"]}
    )
    assert group_name(table) == "Helios"

    # Nothing shared between the names, so nothing is invented.
    unrelated = pd.DataFrame({"company_name": ["Atlas Freight", "Sopra Steria"]})
    assert group_name(unrelated) in {"Atlas Freight", "Sopra Steria"}

    assert group_name(pd.DataFrame()) == "Portfolio company"


def test_the_spend_chain_in_the_document_is_the_one_chain(reported):
    table = load_table(reported)
    document = build_report(reported)

    waterfall = next(
        block for block in _blocks(document) if block.title == "From booked to negotiable"
    )
    drawn = list(waterfall.figures[0].data["step"])
    assert drawn == [step.label for step in spend_chain(table).chain]


def test_the_screen_and_the_exports_read_the_same_blocks(reported):
    table = load_table(reported)
    artifact = load_artifact(reported)

    # visual_blocks() is what ui.summary renders and what both exports draw.
    from_document = [b.title for b in _blocks(build_report(reported)) if b.figures]
    directly = [b.title for b in visual_blocks(table, artifact) if b.figures]

    assert from_document == directly
    assert directly  # a silently empty list would make this test vacuous


def test_the_company_block_carries_one_captioned_figure_per_company(reported):
    blocks = visual_blocks(load_table(reported), load_artifact(reported))

    block = next(b for b in blocks if b.block_id == BY_COMPANY)
    assert len(block.figures) == len(block.labels)
    # The screen picks one by label; a document prints them all, so each says
    # for itself which company and how much it covers.
    assert all(figure.caption for figure in block.figures)


def test_a_run_without_levers_yields_a_shorter_document_not_an_error(defective_run):
    from profiling.data_profiling import confirm_profiling, run_profiling
    from transform.rule_engine import run_rule_engine

    run_profiling(defective_run)
    confirm_profiling(defective_run)
    run_rule_engine(defective_run)

    document = build_report(defective_run)

    assert "Top Levers" not in [section.title for section in document.sections]
    assert document.cover.rows_total > 0


def test_an_empty_table_produces_no_figures_rather_than_failing():
    assert visual_blocks(pd.DataFrame(), None) == []


def _blocks(document):
    return [block for section in document.sections for block in section.blocks]


def test_the_priority_table_carries_the_total_it_is_quoted_by(reported):
    """The cover states a total; the table it comes from has to add up to it."""
    document = build_report(reported)
    priority = next(b for b in _blocks(document) if b.title == "Priority")

    total = priority.table[priority.table["Lever"] == "Total"]
    assert len(total) == 1
    components = priority.table[priority.table["Lever"] != "Total"]
    assert total["Potential (EUR)"].iloc[0] == pytest.approx(
        components["Potential (EUR)"].sum()
    )
