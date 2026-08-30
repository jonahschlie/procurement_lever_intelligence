"""The supplier matching agent: are these two names the same company.

Deterministic similarity decides which pairs are worth asking about; this agent
answers only the grey zone. It sees names and context, never amounts.
"""

import json

from pydantic import BaseModel

from agents.base import AgentDefinition, load_instructions

NAME = "supplier_matching"


class PairVerdict(BaseModel):
    pair_id: int
    same: bool
    confidence: float
    comment: str


class SupplierMatchProposal(BaseModel):
    verdicts: list[PairVerdict]


def definition() -> AgentDefinition:
    return AgentDefinition(
        name=NAME,
        instructions=load_instructions(NAME),
        output_model=SupplierMatchProposal,
    )


def build_input(pairs: list[dict]) -> str:
    """Render the undecided pairs as the agent's user message.

    Each entry: pair_id, left, right, and optional context dicts per side with
    country and categories.
    """
    return json.dumps({"pairs": pairs}, ensure_ascii=False, indent=2)
