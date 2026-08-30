"""The AI cost ledger: booked where the call happens, once per call."""

import pytest
from pydantic import BaseModel

from agents.base import AgentDefinition, run_agent
from core import usage
from core.config import TOKEN_PRICES_USD
from core.pricing import cost_eur, price_for, usd_per_eur
from core.run import create_run, run_path


class Answer(BaseModel):
    ok: bool


class FakeResponse:
    def __init__(self, model, input_tokens, output_tokens):
        self.output_parsed = Answer(ok=True)
        self.model = model
        self.status = "completed"
        self.usage = type("U", (), {"input_tokens": input_tokens, "output_tokens": output_tokens})()


class FakeClient:
    def __init__(self, model="gpt-5-mini-2025-08-07", input_tokens=1000, output_tokens=500):
        self.responses = self
        self._args = (model, input_tokens, output_tokens)

    def parse(self, **kwargs):
        return FakeResponse(*self._args)


def _definition(name="supplier_matching"):
    return AgentDefinition(name=name, instructions="x", output_model=Answer)


# --- pricing ---------------------------------------------------------------


def test_a_dated_model_name_finds_its_price():
    """The API answers with a build, the table holds a family."""
    assert price_for("gpt-5-mini-2025-08-07") == TOKEN_PRICES_USD["gpt-5-mini"]
    # Longest prefix wins, or the mini price would be read off the gpt-5 row.
    assert price_for("gpt-5-mini-2025-08-07") != price_for("gpt-5-2025-08-07")


def test_an_unknown_model_costs_nothing_rather_than_a_guess():
    assert price_for("some-other-model") is None
    assert cost_eur("some-other-model", 1_000_000, 1_000_000) == 0.0


def test_the_cost_is_the_list_price_converted_at_the_ecb_rate():
    price_in, price_out = TOKEN_PRICES_USD["gpt-5-mini"]
    expected_usd = price_in + price_out  # one million of each

    assert cost_eur("gpt-5-mini", 1_000_000, 1_000_000) == pytest.approx(
        expected_usd / usd_per_eur()
    )
    assert cost_eur("gpt-5-mini", 0, 0) == 0.0


# --- the ledger ------------------------------------------------------------


def test_a_run_with_no_calls_totals_zero(run_root):
    run_id = create_run().run_id
    spent = usage.total(run_id)

    assert (spent.calls, spent.tokens, spent.cost_eur) == (0, 0, 0.0)
    assert usage.entries(run_id) == []


def test_every_call_is_booked_once(run_root):
    """The reason this is a ledger and not a sum over the artifacts.

    A stage writes its llm_call into both its proposed and its confirmed artifact,
    so adding those up counts the same call twice -- measured on a real run: ten
    entries for five calls.
    """
    run_id = create_run().run_id
    client = FakeClient(input_tokens=1000, output_tokens=500)

    for _ in range(3):
        run_agent(_definition(), "hi", client=client, run_id=run_id)

    spent = usage.total(run_id)
    assert spent.calls == 3
    assert spent.input_tokens == 3000
    assert spent.output_tokens == 1500
    assert len((run_path(run_id) / usage.LEDGER_NAME).read_text().splitlines()) == 3


def test_calls_are_reported_per_stage(run_root):
    run_id = create_run().run_id
    run_agent(_definition("workbook_triage"), "a", client=FakeClient(), run_id=run_id)
    run_agent(_definition("schema_mapping"), "b", client=FakeClient(), run_id=run_id)
    run_agent(_definition("schema_mapping"), "c", client=FakeClient(), run_id=run_id)

    stages = {row["stage"]: row for row in usage.by_stage(run_id)}
    assert stages["workbook_triage"]["calls"] == 1
    assert stages["schema_mapping"]["calls"] == 2


def test_a_call_without_a_run_is_not_booked_and_does_not_fail(run_root):
    """The tests call agents outside a run, and so may a script."""
    result = run_agent(_definition(), "hi", client=FakeClient())

    assert result.output.ok
    assert usage.entries(create_run().run_id) == []


def test_an_unpriced_model_is_counted_but_flagged(run_root):
    run_id = create_run().run_id
    run_agent(_definition(), "hi", client=FakeClient(model="mystery-model"), run_id=run_id)

    spent = usage.total(run_id)
    assert spent.calls == 1
    assert spent.tokens == 1500  # the tokens are measured either way
    assert spent.cost_eur == 0.0
    assert spent.unpriced_calls == 1  # so zero reads as unknown, not as free


def test_a_failure_to_book_does_not_cost_the_answer(run_root, monkeypatch):
    """The call was already paid for; losing its answer to bookkeeping would be worse."""
    run_id = create_run().run_id
    monkeypatch.setattr(usage, "record", lambda *a, **k: 1 / 0)

    result = run_agent(_definition(), "hi", client=FakeClient(), run_id=run_id)

    assert result.output.ok


def test_the_budget_belongs_to_the_run(run_root):
    with_budget = create_run(budget_eur=2.5).run_id
    without = create_run().run_id

    assert usage.budget(with_budget) == 2.5
    assert usage.budget(without) is None
