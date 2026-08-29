"""The workbook triage agent: what is each sheet for.

Shape is decided deterministically in ingestion.sheet_profile; only the meaning of
a table -- transactions versus an FX lookup versus a supplier list -- needs a
model. Sheets that do not look like tables never reach the agent.
"""

import json

from pydantic import BaseModel

from agents.base import AgentDefinition, load_instructions
from core.models import SheetProfile

NAME = "workbook_triage"


class ProposedRole(BaseModel):
    sheet: str
    role: str
    confidence: float
    comment: str


class WorkbookTriageProposal(BaseModel):
    sheets: list[ProposedRole]


def definition() -> AgentDefinition:
    return AgentDefinition(
        name=NAME,
        instructions=load_instructions(NAME),
        output_model=WorkbookTriageProposal,
    )


def build_input(profiles: list[SheetProfile], filename: str) -> str:
    """Render the candidate sheets as the agent's user message."""
    return json.dumps(
        {
            "workbook": filename,
            "sheets": [
                {
                    "name": profile.name,
                    "rows": profile.rows,
                    "columns": profile.columns,
                    "fill_ratio": profile.fill_ratio,
                    "has_date_column": profile.has_date_column,
                    "has_numeric_column": profile.has_numeric_column,
                    "header": profile.header,
                    "sample_rows": profile.sample_rows,
                }
                for profile in profiles
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
