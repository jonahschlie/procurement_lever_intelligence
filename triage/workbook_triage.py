"""Workbook triage step: give every sheet a role.

Shape is measured deterministically, meaning is asked of a model, and the answer
is checked before it counts. Anything that does not look like a table is marked
as documentation without consulting the agent at all -- a cover letter is not a
judgement call.

Sheets keep their role rather than being thrown away. An FX table is what section
9 needs for currency harmonization and a supplier list is what section 7 needs
for normalization; discarding them here would only mean asking for them again.

Artifacts in ``02_workbook_triage/``:

    workbook_triage.json             what the agent proposed
    workbook_triage_confirmed.json   what the user confirmed, plus the datasets
"""

import re
from logging import Logger
from pathlib import Path

from agents.base import run_agent
from agents.workbook_triage import ProposedRole, build_input, definition
from core.models import (
    Dataset,
    FileManifest,
    LlmCall,
    ReadOptions,
    SHEET_ROLES,
    SheetClassification,
    SheetProfile,
    WorkbookTriage,
    WorkbookTriageArtifact,
)
from core.run import get_logger, record_step, step_path
from ingestion.readers import file_format, read_with_options
from ingestion.sheet_profile import profile_sheets
from ingestion.storage import load_file_manifests, read_source

STEP = "workbook_triage"
ARTIFACT_NAME = "workbook_triage.json"
CONFIRMED_ARTIFACT_NAME = "workbook_triage_confirmed.json"

DOCUMENTATION_COMMENT = "Not a data table: no proper header row over a filled, rectangular body."
SINGLE_TABLE_COMMENT = "The only table in this file, so it is taken to hold the transactions."


def run_workbook_triage(run_id: str, *, client=None) -> WorkbookTriageArtifact:
    logger = get_logger(run_id)
    target = step_path(run_id, STEP)

    workbooks = [
        _triage_file(run_id, manifest, client, logger) for manifest in load_file_manifests(run_id)
    ]
    artifact = WorkbookTriageArtifact(workbooks=workbooks)

    path = target / ARTIFACT_NAME
    path.write_bytes(artifact.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [path])
    logger.info("workbook triage complete: %d file(s)", len(workbooks))
    return artifact


def confirm_triage(
    run_id: str, roles: dict[str, dict[str, str]] | None = None
) -> WorkbookTriageArtifact:
    """Fix the roles and turn everything that is not documentation into a dataset.

    ``roles`` maps a stored filename to the role chosen per sheet. Row counts and
    column names are only determined here, because that is the first point at
    which it is settled which sheets are worth parsing in full.
    """
    roles = roles or {}
    base = load_confirmed_triage(run_id) if has_confirmed(run_id) else load_triage(run_id)
    manifests = {m.stored_filename: m for m in load_file_manifests(run_id)}

    workbooks = [
        workbook.model_copy(
            update={
                "classifications": [
                    _apply_choice(entry, roles.get(workbook.stored_filename, {}))
                    for entry in workbook.classifications
                ]
            }
        )
        for workbook in base.workbooks
    ]
    datasets = [
        dataset
        for workbook in workbooks
        for dataset in _datasets_for(run_id, manifests[workbook.stored_filename], workbook)
    ]
    confirmed = WorkbookTriageArtifact(workbooks=workbooks, datasets=datasets)

    target = step_path(run_id, STEP)
    path = target / CONFIRMED_ARTIFACT_NAME
    path.write_bytes(confirmed.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [target / ARTIFACT_NAME, path])

    logger = get_logger(run_id)
    for dataset in datasets:
        logger.info(
            "dataset %s: role %s, %d rows, %d columns",
            dataset.dataset_id,
            dataset.role,
            dataset.row_count,
            len(dataset.column_names),
        )
    logger.info("triage confirmed: %d dataset(s)", len(datasets))
    return confirmed


def load_triage(run_id: str) -> WorkbookTriageArtifact:
    return _load(step_path(run_id, STEP) / ARTIFACT_NAME)


def load_confirmed_triage(run_id: str) -> WorkbookTriageArtifact:
    return _load(step_path(run_id, STEP) / CONFIRMED_ARTIFACT_NAME)


def load_datasets(run_id: str) -> list[Dataset]:
    return load_confirmed_triage(run_id).datasets


def has_triage(run_id: str) -> bool:
    return (step_path(run_id, STEP) / ARTIFACT_NAME).is_file()


def has_confirmed(run_id: str) -> bool:
    return (step_path(run_id, STEP) / CONFIRMED_ARTIFACT_NAME).is_file()


def reconcile(
    proposals: list[ProposedRole], profiles: list[SheetProfile]
) -> list[SheetClassification]:
    """Decide the roles, taking the agent's answer only where it is admissible."""
    proposed = {proposal.sheet: proposal for proposal in proposals}

    classifications = []
    for profile in profiles:
        if not profile.looks_like_table:
            # Forced, not asked: prose is recognised by shape alone.
            classifications.append(
                SheetClassification(
                    sheet=profile.name,
                    role="documentation",
                    confidence=1.0,
                    comment=DOCUMENTATION_COMMENT,
                )
            )
            continue

        proposal = proposed.get(profile.name)
        if proposal is None:
            classifications.append(
                SheetClassification(
                    sheet=profile.name,
                    role="unknown",
                    confidence=0.0,
                    comment="The agent returned no answer for this sheet.",
                )
            )
            continue

        role = proposal.role if proposal.role in SHEET_ROLES else "unknown"
        comment = proposal.comment.strip() or "No comment given."
        if role != proposal.role:
            comment = f"Agent returned unknown role {proposal.role!r}. {comment}"
        classifications.append(
            SheetClassification(
                sheet=profile.name,
                role=role,
                confidence=round(min(max(proposal.confidence, 0.0), 1.0), 3),
                comment=comment,
            )
        )
    return classifications


def _triage_file(run_id: str, manifest: FileManifest, client, logger: Logger) -> WorkbookTriage:
    data = read_source(run_id, manifest.stored_filename)
    profiles = profile_sheets(data, manifest.file_format, manifest.read_options)
    candidates = [profile for profile in profiles if profile.looks_like_table]

    logger.info(
        "triaging %s: %d sheet(s), %d look like tables",
        manifest.original_filename,
        len(profiles),
        len(candidates),
    )

    if len(candidates) <= 1:
        # Nothing to distinguish, so no reason to spend a model call on it.
        return WorkbookTriage(
            original_filename=manifest.original_filename,
            stored_filename=manifest.stored_filename,
            sheets=profiles,
            classifications=reconcile(
                [
                    ProposedRole(
                        sheet=profile.name,
                        role="transactions",
                        confidence=0.5,
                        comment=SINGLE_TABLE_COMMENT,
                    )
                    for profile in candidates
                ],
                profiles,
            ),
            llm_call=None,
        )

    result = run_agent(
        definition(),
        build_input(candidates, manifest.original_filename),
        client=client,
        logger=logger,
    )
    return WorkbookTriage(
        original_filename=manifest.original_filename,
        stored_filename=manifest.stored_filename,
        sheets=profiles,
        classifications=reconcile(result.output.sheets, profiles),
        llm_call=LlmCall(
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_seconds=result.duration_seconds,
        ),
    )


def _datasets_for(
    run_id: str, manifest: FileManifest, workbook: WorkbookTriage
) -> list[Dataset]:
    data = read_source(run_id, manifest.stored_filename)
    fmt = file_format(manifest.original_filename)

    datasets = []
    for entry in workbook.classifications:
        if entry.role == "documentation":
            continue
        options = manifest.read_options.model_copy(update={"sheet": entry.sheet or None})
        frame = read_with_options(data, fmt, options)
        datasets.append(
            Dataset(
                dataset_id=_dataset_id(manifest.stored_filename, entry.sheet),
                original_filename=manifest.original_filename,
                stored_filename=manifest.stored_filename,
                sheet=entry.sheet or None,
                role=entry.role,
                read_options=options,
                row_count=len(frame),
                column_names=[str(column) for column in frame.columns],
            )
        )
    return datasets


def _apply_choice(entry: SheetClassification, chosen: dict[str, str]) -> SheetClassification:
    selection = chosen.get(entry.sheet)
    if selection is None or selection == entry.role:
        return entry
    return entry.model_copy(
        update={
            "role": selection,
            "confidence": 1.0,
            "comment": "Set by the user.",
            "decided_by": "user",
        }
    )


def _dataset_id(stored_filename: str, sheet: str) -> str:
    stem = Path(stored_filename).stem
    return f"{stem}__{_slug(sheet)}" if sheet else stem


def _slug(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.lower())).strip("_")


def _load(path: Path) -> WorkbookTriageArtifact:
    return WorkbookTriageArtifact.model_validate_json(path.read_bytes())
