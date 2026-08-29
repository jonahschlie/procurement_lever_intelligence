import pytest

from agents.base import AgentError, load_instructions, run_agent
from agents.client import MissingApiKeyError, build_client, model_name
from agents.schema_mapping import (
    NAME,
    ProposedMapping,
    SchemaMappingProposal,
    build_input,
    definition,
)
from core.canonical import CANONICAL_FIELDS
from ingestion.column_profile import build_column_profiles
from ingestion.readers import read_tabular
from tests.conftest import FakeClient


def _proposal():
    return SchemaMappingProposal(
        mappings=[
            ProposedMapping(
                canonical_field="supplier",
                source_column="Vendor",
                confidence=0.95,
                comment="Header and sample values are company names.",
            )
        ]
    )


def test_instructions_carry_the_canonical_schema():
    agent = definition()

    assert load_instructions(NAME) in agent.instructions
    for field in CANONICAL_FIELDS:
        assert f"`{field.key}`" in agent.instructions


def test_agent_input_describes_every_column(sap_csv):
    frame, _ = read_tabular(sap_csv, "sap_export.csv")

    payload = build_input(build_column_profiles(frame), sheet=None)

    for column in frame.columns:
        assert column in payload
    assert "0000123456" in payload  # samples travel with the columns
    assert "sheet" not in payload


def test_agent_input_names_the_sheet_when_there_is_one():
    assert '"sheet": "Transactions"' in build_input([], sheet="Transactions")


def test_run_agent_passes_instructions_and_returns_usage(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    agent = definition()
    client = FakeClient(_proposal())

    result = run_agent(agent, "the input", client=client)

    sent = client.responses.received
    assert sent["instructions"] == agent.instructions
    assert sent["input"] == "the input"
    assert sent["text_format"] is SchemaMappingProposal
    assert sent["model"] == "gpt-5-mini"
    assert result.output.mappings[0].source_column == "Vendor"
    assert (result.input_tokens, result.output_tokens) == (1234, 567)
    assert result.model == "gpt-5-mini-test"


def test_model_is_overridable(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1")
    client = FakeClient(_proposal())

    run_agent(definition(), "input", client=client)

    assert client.responses.received["model"] == "gpt-4.1"
    assert model_name() == "gpt-4.1"


def test_missing_structured_output_is_an_error():
    client = FakeClient(None, status="incomplete")

    with pytest.raises(AgentError, match="no structured output"):
        run_agent(definition(), "input", client=client)


def test_missing_api_key_is_reported_clearly(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(MissingApiKeyError, match=r"\.env"):
        build_client()
