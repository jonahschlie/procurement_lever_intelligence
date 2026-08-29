"""The schema mapping agent (SYSTEMCONCEPT section 7).

The instruction file carries the stable guidance; the canonical field list is
appended at runtime from core.canonical so there is never a second copy of the
schema to keep in sync.
"""

import json

from pydantic import BaseModel

from agents.base import AgentDefinition, load_instructions
from core.canonical import CANONICAL_FIELDS
from core.models import ColumnProfile

NAME = "schema_mapping"


class ProposedMapping(BaseModel):
    canonical_field: str
    source_column: str | None
    confidence: float
    comment: str


class SchemaMappingProposal(BaseModel):
    mappings: list[ProposedMapping]


def definition() -> AgentDefinition:
    return AgentDefinition(
        name=NAME,
        instructions=f"{load_instructions(NAME)}\n{_canonical_reference()}",
        output_model=SchemaMappingProposal,
    )


def build_input(profiles: list[ColumnProfile], sheet: str | None = None) -> str:
    """Render the export's columns as the agent's user message."""
    payload = {
        "columns": [
            {
                "name": profile.name,
                "type": profile.inferred_type,
                "empty_share": profile.null_ratio,
                "distinct_values": profile.distinct_count,
                "samples": profile.sample_values,
            }
            for profile in profiles
        ]
    }
    if sheet:
        payload["sheet"] = sheet
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _canonical_reference() -> str:
    lines = [
        "",
        "## Canonical fields",
        "",
        "| key | field | required | meaning |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| `{field.key}` | {field.label} | {'yes' if field.required else 'no'} | "
        f"{field.description} |"
        for field in CANONICAL_FIELDS
    ]
    return "\n".join(lines)
