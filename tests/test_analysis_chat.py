import json

from agents.analysis_chat import ChatAnswer, build_input, definition
from agents.base import run_agent
from analysis.summary import analysis_context
from tests.conftest import FakeClient


def _answer(text="Because the category duplicates the GL text.", source="profiling", outside=False):
    return ChatAnswer(answer=text, source=source, outside_analysis=outside)


def test_the_agent_is_given_the_analysis_and_nothing_else(lever_run):
    context = analysis_context(lever_run)
    client = FakeClient(_answer())

    run_agent(definition(context), build_input([], "Why is the category analysis off?"), client=client)

    instructions = client.responses.received["instructions"]
    # The facts travel in the instructions; the question in the input.
    assert "spend_chain" in instructions
    assert "Answer only from the context" in instructions
    assert client.responses.received["input"].endswith("Why is the category analysis off?")


def test_no_individual_booking_reaches_the_agent(lever_run):
    context = analysis_context(lever_run)

    text = json.dumps(context)

    for column in ("source_row", "invoice_number", "posting_date", "amount_local"):
        assert column not in text


def test_the_answer_names_where_it_came_from(lever_run):
    result = run_agent(
        definition(analysis_context(lever_run)),
        build_input([], "Why?"),
        client=FakeClient(_answer(source="data quality report")),
    )

    assert result.output.source == "data quality report"
    assert result.output.outside_analysis is False


def test_a_question_outside_the_analysis_is_marked_as_such(lever_run):
    result = run_agent(
        definition(analysis_context(lever_run)),
        build_input([], "What was revenue in 2023?"),
        client=FakeClient(
            _answer("That is not part of this analysis.", source="", outside=True)
        ),
    )

    assert result.output.outside_analysis is True


def test_the_exchange_so_far_is_carried_along(lever_run):
    history = [
        {"role": "user", "content": "What is the biggest lever?"},
        {"role": "assistant", "content": "Supplier consolidation."},
    ]

    payload = build_input(history, "And the second?")

    assert "What is the biggest lever?" in payload
    assert "Supplier consolidation." in payload
    assert payload.strip().endswith("And the second?")


def test_a_long_conversation_is_trimmed_rather_than_grown_without_limit():
    history = [{"role": "user", "content": f"q{i}"} for i in range(30)]

    payload = build_input(history, "latest")

    assert "q29" in payload
    assert "q0" not in payload
