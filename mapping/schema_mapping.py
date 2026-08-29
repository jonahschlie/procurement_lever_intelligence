"""Schema mapping step: run the agent, then decide deterministically what counts.

The agent proposes, this module disposes. It returns free-form field and column
names, so everything it says is checked against the canonical schema and the
actual columns of the file before it becomes an artifact. A hallucinated column
cannot enter the pipeline -- it is turned into an honest gap with a note saying
what happened.

Artifacts in ``02_schema_mapping/``:

    schema_mapping.json             what the agent proposed
    schema_mapping_confirmed.json   what the user confirmed

Both are kept. Which decisions were the model's and which were a person's has to
stay distinguishable afterwards.
"""

from logging import Logger
from pathlib import Path

from agents.base import run_agent
from agents.schema_mapping import ProposedMapping, build_input, definition
from core.canonical import CANONICAL_FIELDS, CANONICAL_KEYS
from core.models import (
    Dataset,
    DatasetMapping,
    FieldMapping,
    LlmCall,
    SchemaMappingArtifact,
)
from core.run import get_logger, record_step, step_path
from ingestion.column_profile import build_column_profiles
from ingestion.storage import load_dataframe
from triage.workbook_triage import load_datasets

STEP = "schema_mapping"
ARTIFACT_NAME = "schema_mapping.json"
CONFIRMED_ARTIFACT_NAME = "schema_mapping_confirmed.json"


def run_schema_mapping(run_id: str, *, client=None) -> SchemaMappingArtifact:
    """Map the run's transaction datasets onto the canonical schema.

    Only datasets triaged as transactions are mapped. An FX table or a supplier
    list has no canonical procurement schema to be mapped onto, and asking the
    agent about one would burn a call to be told exactly that.
    """
    logger = get_logger(run_id)
    target = step_path(run_id, STEP)
    agent = definition()

    transactional = [d for d in load_datasets(run_id) if d.role == "transactions"]
    if not transactional:
        logger.warning("schema mapping: no dataset was triaged as transactions")

    mapped = [_map_dataset(run_id, dataset, agent, client, logger) for dataset in transactional]
    artifact = SchemaMappingArtifact(datasets=mapped)

    path = target / ARTIFACT_NAME
    path.write_bytes(artifact.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [path])
    logger.info("schema mapping complete: %d dataset(s)", len(mapped))
    return artifact


def confirm_mapping(
    run_id: str, selections: dict[str, dict[str, str | None]]
) -> SchemaMappingArtifact:
    """Persist the user's decisions.

    ``selections`` maps a dataset id to the source column chosen per
    canonical field. Anything that differs from the proposal is marked as decided
    by the user, so the artifact records who is answerable for each field.
    """
    # Build on an earlier confirmation when there is one: with several datasets the
    # user confirms them one tab at a time, and starting from the proposal each time
    # would silently discard the tabs already dealt with.
    base = load_confirmed(run_id) if has_confirmed(run_id) else load_artifact(run_id)
    datasets = [
        dataset.model_copy(
            update={
                "mappings": [
                    _apply_choice(mapping, selections.get(dataset.dataset_id, {}))
                    for mapping in dataset.mappings
                ]
            }
        )
        for dataset in base.datasets
    ]
    confirmed = SchemaMappingArtifact(datasets=datasets)

    target = step_path(run_id, STEP)
    path = target / CONFIRMED_ARTIFACT_NAME
    path.write_bytes(confirmed.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [target / ARTIFACT_NAME, path])

    edited = sum(m.decided_by == "user" for d in datasets for m in d.mappings)
    get_logger(run_id).info("schema mapping confirmed, %d field(s) decided by the user", edited)
    return confirmed


def load_artifact(run_id: str) -> SchemaMappingArtifact:
    return _load(step_path(run_id, STEP) / ARTIFACT_NAME)


def load_confirmed(run_id: str) -> SchemaMappingArtifact:
    return _load(step_path(run_id, STEP) / CONFIRMED_ARTIFACT_NAME)


def has_mapping(run_id: str) -> bool:
    return (step_path(run_id, STEP) / ARTIFACT_NAME).is_file()


def has_confirmed(run_id: str) -> bool:
    return (step_path(run_id, STEP) / CONFIRMED_ARTIFACT_NAME).is_file()


def reconcile(proposals: list[ProposedMapping], columns: list[str]) -> list[FieldMapping]:
    """Turn what the agent said into something the pipeline can rely on.

    Unknown canonical keys are dropped, missing ones are added as gaps, columns
    the file does not have are refused, a column claimed twice is flagged on the
    second claim, and confidence is clamped into range.
    """
    proposed = {p.canonical_field: p for p in proposals if p.canonical_field in CANONICAL_KEYS}
    available = set(columns)
    claimed: dict[str, str] = {}
    mappings = []

    for field in CANONICAL_FIELDS:
        proposal = proposed.get(field.key)
        if proposal is None:
            mappings.append(
                FieldMapping(
                    canonical_field=field.key,
                    source_column=None,
                    confidence=0.0,
                    comment="The agent returned no answer for this field.",
                )
            )
            continue

        source = proposal.source_column
        comment = proposal.comment.strip() or "No comment given."
        confidence = min(max(proposal.confidence, 0.0), 1.0)

        if source is not None and source not in available:
            comment = f"Agent proposed {source!r}, which is not a column in this file."
            source, confidence = None, 0.0
        elif source is not None and source in claimed:
            comment = f"{comment} Note: also proposed for '{claimed[source]}'."

        if source is not None:
            claimed.setdefault(source, field.key)

        mappings.append(
            FieldMapping(
                canonical_field=field.key,
                source_column=source,
                confidence=round(confidence, 3),
                comment=comment,
            )
        )
    return mappings


def _map_dataset(
    run_id: str, dataset: Dataset, agent, client, logger: Logger
) -> DatasetMapping:
    frame = load_dataframe(run_id, dataset)
    profiles = build_column_profiles(frame)
    logger.info(
        "mapping %s: %d column(s) sent to the agent",
        dataset.dataset_id,
        len(profiles),
    )

    result = run_agent(
        agent,
        build_input(profiles, dataset.sheet),
        client=client,
        logger=logger,
    )
    mappings = reconcile(result.output.mappings, [str(c) for c in frame.columns])

    matched = sum(m.source_column is not None for m in mappings)
    logger.info(
        "mapped %s: %d of %d canonical fields matched",
        dataset.dataset_id,
        matched,
        len(CANONICAL_FIELDS),
    )
    return DatasetMapping(
        dataset_id=dataset.dataset_id,
        original_filename=dataset.original_filename,
        sheet=dataset.sheet,
        column_profiles=profiles,
        mappings=mappings,
        llm_call=LlmCall(
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_seconds=result.duration_seconds,
        ),
    )


def _apply_choice(mapping: FieldMapping, chosen: dict[str, str | None]) -> FieldMapping:
    if mapping.canonical_field not in chosen:
        return mapping
    selection = chosen[mapping.canonical_field]
    if selection == mapping.source_column:
        return mapping
    # A confirmed human decision is certain by definition; decided_by keeps the
    # provenance visible so this is never mistaken for model confidence.
    return mapping.model_copy(
        update={
            "source_column": selection,
            "confidence": 1.0,
            "comment": "Set by the user.",
            "decided_by": "user",
        }
    )


def _load(path: Path) -> SchemaMappingArtifact:
    return SchemaMappingArtifact.model_validate_json(path.read_bytes())
