"""Questions the analysis raises for the people who know the business.

Booking data measures what happened; it cannot say why. The agent turns findings
into questions for a meeting -- intent, policy and history, which no column holds.
"""

import json

from pydantic import BaseModel

from agents.base import AgentDefinition, load_instructions

NAME = "sme_questions"


class SmeQuestion(BaseModel):
    question: str
    rationale: str
    addressee: str
    unlocks: str


class SmeQuestionProposal(BaseModel):
    questions: list[SmeQuestion]


def definition() -> AgentDefinition:
    return AgentDefinition(
        name=NAME, instructions=load_instructions(NAME), output_model=SmeQuestionProposal
    )


def build_input(context: dict) -> str:
    return json.dumps(context, ensure_ascii=False, indent=2)
