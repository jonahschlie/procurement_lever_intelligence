"""Spend classification: which cost types procurement can actually influence.

Payroll, taxes, interest and provisions sit in the same ledger as consulting and
freight. Counting them as spend overstates every lever derived later, so the
distinction is drawn once, here, over the distinct cost types rather than over
the rows -- a chart of accounts has tens of labels, not tens of thousands.
"""

from pathlib import Path

import pandas as pd

from agents.base import run_agent
from agents.spend_addressability import build_input, definition
from core.models import CostTypeClass, LlmCall, SpendClassificationArtifact
from core.run import get_logger, record_step, step_path
from core.table import load_table, write_table

STEP = "spend_classification"
ARTIFACT_NAME = "spend_classification.json"
CONFIRMED_ARTIFACT_NAME = "spend_classification_confirmed.json"

# Cost types are read from whichever column actually describes the purchase.
SOURCE_COLUMNS = ("gl_description", "category")
MAX_EXAMPLE_SUPPLIERS = 4


def run_spend_classification(run_id: str, *, client=None) -> SpendClassificationArtifact:
    logger = get_logger(run_id)
    table = load_table(run_id)
    column = _source_column(table)

    if column is None:
        logger.warning("spend classification: no cost type column, everything stays addressable")
        artifact = SpendClassificationArtifact(source_column="", cost_types=[], llm_call=None)
        return _store(run_id, artifact, ARTIFACT_NAME)

    summary = _cost_types(table, column)
    logger.info("spend classification: %d distinct cost type(s) from %r", len(summary), column)

    result = run_agent(
        definition(), build_input(summary), client=client, logger=logger, run_id=run_id
    )
    verdicts = {verdict.cost_type: verdict for verdict in result.output.verdicts}

    cost_types = []
    for entry in summary:
        verdict = verdicts.get(entry["cost_type"])
        cost_types.append(
            CostTypeClass(
                cost_type=entry["cost_type"],
                # No answer means addressable: excluding spend nobody judged would
                # remove it from the analysis without anyone noticing.
                addressable=verdict.addressable if verdict else True,
                confidence=round(min(max(verdict.confidence, 0.0), 1.0), 3) if verdict else 0.0,
                comment=(
                    verdict.comment.strip()
                    if verdict and verdict.comment.strip()
                    else "The agent returned no verdict for this cost type."
                ),
                spend=entry["spend"],
                rows=entry["rows"],
            )
        )

    artifact = SpendClassificationArtifact(
        source_column=column,
        cost_types=cost_types,
        llm_call=LlmCall(
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_seconds=result.duration_seconds,
        ),
    )
    non_addressable = sum(entry.spend for entry in cost_types if not entry.addressable)
    logger.info(
        "spend classification: %d of %d cost type(s) not addressable, %.2f of spend",
        sum(1 for entry in cost_types if not entry.addressable),
        len(cost_types),
        non_addressable,
    )
    return _store(run_id, artifact, ARTIFACT_NAME)


def confirm_classification(
    run_id: str, decisions: dict[str, bool] | None = None
) -> SpendClassificationArtifact:
    """Apply the user's corrections and write the row-level flag."""
    decisions = decisions or {}
    artifact = load_artifact(run_id)

    cost_types = []
    for entry in artifact.cost_types:
        chosen = decisions.get(entry.cost_type, entry.addressable)
        cost_types.append(
            entry.model_copy(
                update={"addressable": chosen, "decided_by": "user"}
                if chosen != entry.addressable
                else {}
            )
        )
    confirmed = artifact.model_copy(update={"cost_types": cost_types})

    _write_column(run_id, confirmed)
    stored = _store(run_id, confirmed, CONFIRMED_ARTIFACT_NAME)
    get_logger(run_id).info(
        "spend classification confirmed: %d cost type(s) marked not addressable",
        sum(1 for entry in cost_types if not entry.addressable),
    )
    return stored


def load_artifact(run_id: str) -> SpendClassificationArtifact:
    return _load(step_path(run_id, STEP) / ARTIFACT_NAME)


def load_confirmed(run_id: str) -> SpendClassificationArtifact:
    return _load(step_path(run_id, STEP) / CONFIRMED_ARTIFACT_NAME)


def has_artifact(run_id: str) -> bool:
    return (step_path(run_id, STEP) / ARTIFACT_NAME).is_file()


def non_addressable_mask(table: pd.DataFrame, artifact: SpendClassificationArtifact) -> pd.Series:
    if not artifact.source_column or artifact.source_column not in table.columns:
        return pd.Series(False, index=table.index)
    excluded = {entry.cost_type for entry in artifact.cost_types if not entry.addressable}
    return table[artifact.source_column].astype(str).str.strip().isin(excluded)


def _source_column(table: pd.DataFrame) -> str | None:
    for column in SOURCE_COLUMNS:
        if column in table.columns and (table[column].astype(str).str.strip() != "").any():
            return column
    return None


def _cost_types(table: pd.DataFrame, column: str) -> list[dict]:
    rows = table[table["include_spend_analysis"].astype(bool)] if "include_spend_analysis" in table else table
    labels = rows[column].astype(str).str.strip()
    amount = rows["amount_eur"] if "amount_eur" in rows else rows["amount_local_value"]

    summary = []
    for label, index in labels[labels != ""].groupby(labels).groups.items():
        suppliers = [
            name
            for name in rows.loc[index, "supplier"].astype(str).str.strip().unique()
            if name
        ][:MAX_EXAMPLE_SUPPLIERS]
        summary.append(
            {
                "cost_type": str(label),
                "spend": round(float(amount.loc[index].sum()), 2),
                "rows": int(len(index)),
                "example_suppliers": suppliers,
            }
        )
    return sorted(summary, key=lambda entry: -entry["spend"])


def _write_column(run_id: str, artifact: SpendClassificationArtifact) -> None:
    table = load_table(run_id)
    table["flag_non_addressable"] = non_addressable_mask(table, artifact)
    write_table(run_id, table, STEP, note="addressability of each cost type")


def _store(run_id: str, artifact: SpendClassificationArtifact, name: str):
    target = step_path(run_id, STEP)
    (target / name).write_bytes(artifact.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [target / name])
    return artifact


def _load(path: Path) -> SpendClassificationArtifact:
    return SpendClassificationArtifact.model_validate_json(path.read_bytes())
