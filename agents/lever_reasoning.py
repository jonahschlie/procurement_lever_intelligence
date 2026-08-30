"""The lever reasoning agent: what to do about what was measured.

Every figure is computed deterministically before this agent runs. It receives
aggregates only -- never a row -- and returns narrative and ordering. The output
model has no numeric field at all, so it cannot contradict the arithmetic shown
beside it.
"""

import json

from pydantic import BaseModel

from agents.base import AgentDefinition, load_instructions

NAME = "lever_reasoning"


class LeverNarrative(BaseModel):
    lever_id: str
    opportunity: str
    next_steps: list[str]


class LeverReasoningProposal(BaseModel):
    levers: list[LeverNarrative]
    priority_rationale: str
    recommended_order: list[str]
    order_reason: str


def definition() -> AgentDefinition:
    return AgentDefinition(
        name=NAME,
        instructions=load_instructions(NAME),
        output_model=LeverReasoningProposal,
    )


def build_input(levers: list[dict], benchmark: list[dict]) -> str:
    return json.dumps(
        {"levers": levers, "companies": benchmark}, ensure_ascii=False, indent=2
    )
