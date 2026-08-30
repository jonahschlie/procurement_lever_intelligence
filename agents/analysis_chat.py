"""A chat grounded in one run's artifacts, and nothing else.

The context is assembled once per run from the stage artifacts. The agent answers
from it or says it cannot -- it never computes, so an answer can never contradict
a figure shown beside it.
"""

import json

from pydantic import BaseModel

from agents.base import AgentDefinition, load_instructions

NAME = "analysis_chat"


class ChatAnswer(BaseModel):
    answer: str
    # Which stage the answer rests on, so a reader can go and check it.
    source: str
    # True when the question cannot be answered from this analysis.
    outside_analysis: bool = False


def definition(context: dict) -> AgentDefinition:
    """Instructions plus this run's facts -- the agent's whole world."""
    return AgentDefinition(
        name=NAME,
        instructions=(
            f"{load_instructions(NAME)}\n\n## The analysis\n\n"
            f"```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```"
        ),
        output_model=ChatAnswer,
    )


def build_input(history: list[dict], question: str) -> str:
    """The exchange so far, then the new question."""
    lines = [f"{turn['role'].upper()}: {turn['content']}" for turn in history[-8:]]
    lines.append(f"USER: {question}")
    return "\n\n".join(lines)
