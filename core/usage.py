"""The ledger of model calls, one per run.

Written where the call happens and nowhere else. Deriving it from the artifacts
afterwards would double-count: a stage writes its `llm_call` into both the
proposed and the confirmed artifact, so a scan of a real run found ten entries
for five calls.

One line appended per call, so a run that was interrupted still knows what it
already spent.
"""

import json
from datetime import datetime, timezone

from core.models import Usage, UsageEntry
from core.pricing import cost_eur, price_for
from core.run import run_path

LEDGER_NAME = "usage.jsonl"


def record(
    run_id: str, stage: str, model: str, input_tokens: int, output_tokens: int
) -> UsageEntry:
    entry = UsageEntry(
        at=datetime.now(timezone.utc),
        stage=stage,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_eur=cost_eur(model, input_tokens, output_tokens),
    )
    path = run_path(run_id) / LEDGER_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as ledger:
        ledger.write(entry.model_dump_json() + "\n")
    return entry


def entries(run_id: str) -> list[UsageEntry]:
    path = run_path(run_id) / LEDGER_NAME
    if not path.is_file():
        return []
    return [
        UsageEntry.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def total(run_id: str) -> Usage:
    """A run with no calls yet totals zero rather than failing."""
    booked = entries(run_id)
    return Usage(
        calls=len(booked),
        input_tokens=sum(entry.input_tokens for entry in booked),
        output_tokens=sum(entry.output_tokens for entry in booked),
        cost_eur=sum(entry.cost_eur for entry in booked),
        # A model the price table does not know costs zero, which would otherwise
        # read as "free" rather than "unpriced".
        unpriced_calls=sum(1 for entry in booked if price_for(entry.model) is None),
    )


def by_stage(run_id: str) -> list[dict]:
    """Per stage, in the order the stages first ran."""
    grouped: dict[str, dict] = {}
    for entry in entries(run_id):
        row = grouped.setdefault(
            entry.stage, {"stage": entry.stage, "calls": 0, "tokens": 0, "cost_eur": 0.0}
        )
        row["calls"] += 1
        row["tokens"] += entry.input_tokens + entry.output_tokens
        row["cost_eur"] += entry.cost_eur
    return list(grouped.values())


def budget(run_id: str) -> float | None:
    from core.run import load_run

    try:
        return load_run(run_id).budget_eur
    except (FileNotFoundError, json.JSONDecodeError):
        return None
