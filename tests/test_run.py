from datetime import datetime, timezone

import pytest

from core import run as run_module
from core.run import (
    RUN_MANIFEST_NAME,
    create_run,
    get_logger,
    load_run,
    record_step,
    run_path,
    step_dir_name,
    step_path,
)

FROZEN = datetime(2026, 8, 29, 23, 30, 45, tzinfo=timezone.utc)


@pytest.fixture
def frozen_clock(monkeypatch):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return FROZEN

    monkeypatch.setattr(run_module, "datetime", _Frozen)


def test_creates_workspace_with_manifest_and_log(run_root):
    manifest = create_run()

    workspace = run_root / manifest.run_id
    assert manifest.run_id.startswith("run_")
    assert (workspace / RUN_MANIFEST_NAME).is_file()
    assert (workspace / "logs" / "run.log").is_file()
    assert manifest.steps == []
    assert load_run(manifest.run_id) == manifest


def test_run_id_follows_the_timestamp_scheme(run_root, frozen_clock):
    assert create_run().run_id == "run_20260829_233045"


def test_second_run_in_the_same_second_gets_a_suffix(run_root, frozen_clock):
    first = create_run()
    second = create_run()

    assert (first.run_id, second.run_id) == ("run_20260829_233045", "run_20260829_233045_2")
    assert (run_root / first.run_id / RUN_MANIFEST_NAME).is_file()


def test_step_directory_is_numbered_by_pipeline_position(run_root):
    manifest = create_run()

    assert step_dir_name("ingestion") == "01_ingestion"
    assert step_path(manifest.run_id, "ingestion") == run_path(manifest.run_id) / "01_ingestion"
    assert step_path(manifest.run_id, "ingestion").is_dir()


def test_unknown_step_is_rejected(run_root):
    with pytest.raises(ValueError, match="unknown pipeline step"):
        step_dir_name("not_a_step")


def test_record_step_stores_relative_artifact_paths(run_root):
    manifest = create_run()
    target = step_path(manifest.run_id, "ingestion")
    artifact = target / "ingestion.json"
    artifact.write_text("[]", encoding="utf-8")

    updated = record_step(manifest.run_id, "ingestion", [artifact])

    assert [step.step for step in updated.steps] == ["ingestion"]
    assert updated.steps[0].artifacts == ["01_ingestion/ingestion.json"]
    assert load_run(manifest.run_id).steps == updated.steps


def test_repeating_a_step_replaces_its_record(run_root):
    manifest = create_run()
    target = step_path(manifest.run_id, "ingestion")
    first = target / "a.json"
    second = target / "b.json"
    for path in (first, second):
        path.write_text("[]", encoding="utf-8")

    record_step(manifest.run_id, "ingestion", [first])
    updated = record_step(manifest.run_id, "ingestion", [second])

    assert len(updated.steps) == 1
    assert updated.steps[0].artifacts == ["01_ingestion/b.json"]


def test_logger_is_not_attached_twice_behind_a_symlink(tmp_path, monkeypatch):
    # FileHandler stores an abspath, which keeps symlinks; matching it against a
    # resolved path would attach a second handler on every call and double every line.
    real = tmp_path / "real_runs"
    real.mkdir()
    link = tmp_path / "linked_runs"
    link.symlink_to(real)
    monkeypatch.setenv("PLI_RUNS_DIR", str(link))

    manifest = create_run()
    get_logger(manifest.run_id).info("once")

    log = (real / manifest.run_id / "logs" / "run.log").read_text(encoding="utf-8")
    assert log.count("once") == 1
    assert log.count("run created") == 1


def test_logger_is_not_attached_twice(run_root):
    manifest = create_run()

    get_logger(manifest.run_id).info("first")
    get_logger(manifest.run_id).info("second")

    log = (run_path(manifest.run_id) / "logs" / "run.log").read_text(encoding="utf-8")
    assert log.count("first") == 1
    assert log.count("second") == 1
    assert log.count("run created") == 1
