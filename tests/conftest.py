import logging
from pathlib import Path
from types import SimpleNamespace

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


@pytest.fixture(autouse=True)
def run_root(tmp_path, monkeypatch):
    """Point run workspaces at a throwaway directory for the duration of a test.

    Autouse on purpose. Forgetting to request it would silently write test runs
    into the developer's real runs/ directory, which is exactly what happened
    once before this became automatic. Tests that need the path still ask for it
    by name and get this same directory.
    """
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


@pytest.fixture
def portfolio_xlsx() -> bytes:
    """A submission workbook: cover letter, instructions, spend data, master, FX."""
    return (FIXTURES / "portfolio_workbook.xlsx").read_bytes()


class FakeResponses:
    """Stand-in for client.responses, capturing what an agent was asked."""

    def __init__(self, parsed, *, model="gpt-5-mini-test", status="completed"):
        self._parsed = parsed
        self._model = model
        self._status = status
        self.received: dict = {}

    def parse(self, **kwargs):
        self.received = kwargs
        return SimpleNamespace(
            output_parsed=self._parsed,
            model=self._model,
            status=self._status,
            usage=SimpleNamespace(input_tokens=1234, output_tokens=567),
        )


class FakeClient:
    def __init__(self, parsed, **kwargs):
        self.responses = FakeResponses(parsed, **kwargs)
