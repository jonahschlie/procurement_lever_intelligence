import json

import pandas as pd
import pytest

from agents.sme_questions import SmeQuestion, SmeQuestionProposal
from analysis.summary import analysis_context, build_summary, load_summary
from core.run import load_run
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
def analysed(lever_run):
    return lever_run


def test_the_summary_gathers_a_section_per_stage(analysed):
    summary = build_summary(analysed, client=FakeClient(_proposal()))

    titles = [section.title for section in summary.sections]
    assert "What the data quality checks found" in titles
    assert "What can be acted on" in titles
    assert all(section.headline for section in summary.sections)


def test_a_stage_that_never_ran_is_left_out_rather_than_failing(defective_run):
    # Only the canonical table exists here: no profiling, no levers, no currency.
    summary = build_summary(defective_run, client=FakeClient(_proposal()))

    assert summary.sections == [] or all(s.headline for s in summary.sections)
    assert summary.run_id == defective_run


def test_the_questions_are_recorded_with_who_should_answer(analysed):
    summary = build_summary(analysed, client=FakeClient(_proposal()))

    assert len(summary.sme_questions) == 1
    assert summary.sme_questions[0].addressee == "procurement"
    assert summary.llm_call is not None
    assert load_summary(analysed) == summary


def test_a_failing_question_agent_does_not_cost_the_summary(analysed):
    class Broken:
        class responses:
            @staticmethod
            def parse(**kwargs):
                raise RuntimeError("no")

    summary = build_summary(analysed, client=Broken())

    assert summary.sections  # the measured part survives
    assert summary.sme_questions == []


def test_the_step_is_recorded(analysed):
    build_summary(analysed, client=FakeClient(_proposal()))

    assert "executive_summary" in [step.step for step in load_run(analysed).steps]


# --- the context both agents read -----------------------------------------


def test_the_context_holds_aggregates_and_never_a_booking(analysed):
    context = analysis_context(analysed)

    text = json.dumps(context)
    assert "source_row" not in text
    assert "invoice_number" not in text
    assert "spend_chain" in context
    assert "levers" in context


def test_the_context_carries_the_spend_chain_as_computed(analysed):
    from analysis.spend_report import build_spend_report

    context = analysis_context(analysed)
    report = build_spend_report(analysed)

    assert [entry["step"] for entry in context["spend_chain"]] == [
        step.label for step in report.chain
    ]


def test_the_context_survives_a_run_without_levers(defective_run):
    context = analysis_context(defective_run)

    assert context["run_id"] == defective_run
    assert "levers" not in context
