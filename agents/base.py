"""Running an agent: instructions in, validated structured output out.

Every model call in the platform goes through here. An agent is defined by its
instruction file and the pydantic model it must return; the structure is enforced
by the API rather than parsed out of prose, so a malformed answer fails loudly
instead of quietly becoming nonsense downstream.

Adding an agent means adding an instruction file and an output model -- this
module does not change.
"""

import time
from dataclasses import dataclass
from logging import Logger
from pathlib import Path

from pydantic import BaseModel

from agents.client import build_client, model_name

INSTRUCTIONS_DIR = Path(__file__).parent / "instructions"


class AgentError(RuntimeError):
    """Raised when a model returns no usable structured output."""


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    instructions: str
    output_model: type[BaseModel]


@dataclass(frozen=True)
class AgentRun:
    """An agent's answer together with what it cost to get it."""

    output: BaseModel
    model: str
    input_tokens: int
    output_tokens: int
    duration_seconds: float


def load_instructions(name: str) -> str:
    return (INSTRUCTIONS_DIR / f"{name}.md").read_text(encoding="utf-8")


def run_agent(
    definition: AgentDefinition,
    user_input: str,
    *,
    client=None,
    logger: Logger | None = None,
) -> AgentRun:
    """Call the model. ``client`` is the seam tests use to stay offline."""
    client = client or build_client()
    started = time.perf_counter()
    response = client.responses.parse(
        model=model_name(),
        instructions=definition.instructions,
        input=user_input,
        text_format=definition.output_model,
    )
    duration = time.perf_counter() - started

    if response.output_parsed is None:
        raise AgentError(
            f"agent {definition.name!r} returned no structured output (status: {response.status})"
        )

    usage = response.usage
    result = AgentRun(
        output=response.output_parsed,
        model=response.model,
        input_tokens=usage.input_tokens if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
        duration_seconds=round(duration, 2),
    )
    if logger is not None:
        logger.info(
            "agent %s answered via %s in %.2fs (%d in / %d out tokens)",
            definition.name,
            result.model,
            result.duration_seconds,
            result.input_tokens,
            result.output_tokens,
        )
    return result
