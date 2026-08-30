"""The spend addressability agent: can procurement influence this cost type.

A chart of accounts holds 20-80 distinct labels regardless of ERP or language, so
one call classifies all of them. The agent reads meaning rather than keywords --
a keyword list is exactly the mistake the category check already taught us to
avoid.
"""

import json

from pydantic import BaseModel

from agents.base import AgentDefinition, load_instructions

NAME = "spend_addressability"


class CostTypeVerdict(BaseModel):
    cost_type: str
    addressable: bool
    confidence: float
    comment: str


class AddressabilityProposal(BaseModel):
    verdicts: list[CostTypeVerdict]


def definition() -> AgentDefinition:
    return AgentDefinition(
        name=NAME,
        instructions=load_instructions(NAME),
        output_model=AddressabilityProposal,
    )


def build_input(cost_types: list[dict]) -> str:
    return json.dumps({"cost_types": cost_types}, ensure_ascii=False, indent=2)
