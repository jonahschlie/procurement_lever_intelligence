from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """Point storage at a throwaway directory for the duration of a test."""
    monkeypatch.setenv("PLI_DATA_DIR", str(tmp_path))
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
