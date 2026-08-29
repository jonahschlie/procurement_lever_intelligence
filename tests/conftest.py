import logging
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_run_loggers():
    """Detach run loggers after each test.

    They are cached globally by name, and run ids repeat across tests that execute
    within the same second, so a handler left behind would keep writing into a
    temporary directory that no longer exists.
    """
    yield
    for name, logger in list(logging.root.manager.loggerDict.items()):
        if name.startswith("pli.run.") and isinstance(logger, logging.Logger):
            for handler in logger.handlers:
                handler.close()
            logger.handlers.clear()


@pytest.fixture
def run_root(tmp_path, monkeypatch):
    """Point run workspaces at a throwaway directory for the duration of a test."""
    monkeypatch.setenv("PLI_RUNS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def sap_csv() -> bytes:
    return (FIXTURES / "sap_export.csv").read_bytes()


@pytest.fixture
def oracle_csv() -> bytes:
    return (FIXTURES / "oracle_export.csv").read_bytes()


@pytest.fixture
def dynamics_xlsx() -> bytes:
    return (FIXTURES / "dynamics_export.xlsx").read_bytes()
