"""Run workspaces: one directory per execution of the pipeline.

A run owns its inputs, the artifact every pipeline step produces, and the log of
what happened. Keeping them together is what makes a result auditable -- the
spend figures, the export they came from and the steps in between all sit in the
same place.

Layout::

    runs/run_<YYYYMMDD_HHMMSS>/
        run.json          # RunManifest, extended as steps complete
        logs/run.log      # every step, chronologically
        01_ingestion/     # one numbered directory per pipeline step
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from core.config import runs_dir
from core.models import RunManifest, StepRecord

# Pipeline order per SYSTEMCONCEPT; the position drives each step's directory prefix.
# Later stages are appended here as they are built.
PIPELINE_STEPS = (
    "ingestion",
    "workbook_triage",
    "schema_mapping",
    "canonical_table",
    "profiling",
    "rule_engine",
    "currency",
    "supplier_normalization",
    "spend_classification",
    "levers",
    "executive_summary",
)

RUN_MANIFEST_NAME = "run.json"
LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "run.log"


def create_run() -> RunManifest:
    created_at = datetime.now(timezone.utc)
    manifest = RunManifest(run_id=_available_run_id(created_at), created_at=created_at)
    (run_path(manifest.run_id) / LOG_DIR_NAME).mkdir(parents=True)
    _write_manifest(manifest.run_id, manifest)
    get_logger(manifest.run_id).info("run created")
    return manifest


def run_path(run_id: str) -> Path:
    return runs_dir() / run_id


def step_dir_name(step: str) -> str:
    if step not in PIPELINE_STEPS:
        raise ValueError(
            f"unknown pipeline step {step!r}; expected one of {', '.join(PIPELINE_STEPS)}"
        )
    return f"{PIPELINE_STEPS.index(step) + 1:02d}_{step}"


def step_path(run_id: str, step: str) -> Path:
    path = run_path(run_id) / step_dir_name(step)
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_run(run_id: str) -> RunManifest:
    path = run_path(run_id) / RUN_MANIFEST_NAME
    return RunManifest.model_validate_json(path.read_text(encoding="utf-8"))


def record_step(run_id: str, step: str, artifacts: list[Path]) -> RunManifest:
    """Note a completed step and the artifacts it wrote in run.json."""
    manifest = load_run(run_id)
    root = run_path(run_id)
    record = StepRecord(
        step=step,
        completed_at=datetime.now(timezone.utc),
        artifacts=sorted(str(path.relative_to(root)) for path in artifacts),
    )
    # A re-run of a step replaces its record rather than appending a second one.
    manifest.steps = [entry for entry in manifest.steps if entry.step != step] + [record]
    _write_manifest(run_id, manifest)
    return manifest


def get_logger(run_id: str) -> logging.Logger:
    """Logger writing into the run's own log file, attached exactly once.

    Streamlit re-executes the script on every interaction, so this is called
    repeatedly for the same run. The check looks for this run's file handler
    specifically rather than for any handler at all -- other parties attach their
    own (pytest does), and a bare emptiness check would then skip the setup.
    """
    logger = logging.getLogger(f"pli.run.{run_id}")
    log_file = run_path(run_id) / LOG_DIR_NAME / LOG_FILE_NAME
    if not _has_file_handler(logger, log_file):
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def _has_file_handler(logger: logging.Logger, log_file: Path) -> bool:
    # Both sides are resolved: FileHandler records baseFilename via os.path.abspath,
    # which leaves symlinks in place, so comparing raw strings misses a match
    # whenever the runs directory sits behind one (/var -> /private/var on macOS).
    target = log_file.resolve()
    return any(
        isinstance(handler, logging.FileHandler)
        and Path(handler.baseFilename).resolve() == target
        for handler in logger.handlers
    )


def _available_run_id(created_at: datetime) -> str:
    base = f"run_{created_at:%Y%m%d_%H%M%S}"
    candidate, suffix = base, 1
    while (runs_dir() / candidate).exists():
        suffix += 1
        candidate = f"{base}_{suffix}"
    return candidate


def _write_manifest(run_id: str, manifest: RunManifest) -> None:
    """Write back to the directory the manifest was read from.

    Deliberately not run_path(manifest.run_id): a run directory that was copied
    or renamed still carries its original id inside, and trusting that would
    record this run's steps into a different run's manifest.
    """
    path = run_path(run_id) / RUN_MANIFEST_NAME
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
