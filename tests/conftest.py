import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

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


# A canonical table with one of every defect the profiling checks look for.
# Source rows are numbered as they would be in a real export, header included.
_DEFECTIVE_ROWS = [
    # supplier, amount_local, amount_group, currency, posting, document, invoice, po, gl_account, gl_desc, category, company
    ("Atlas Freight", "1000.00", "1000.00", "EUR", "2024-01-15", "2024-01-15", "INV1", "PO1", "6000", "Freight costs", "Logistics", "A"),
    ("Sopra Steria", "2000.00", "2000.00", "EUR", "2024-02-10", "2024-02-10", "INV2", "PO2", "6200", "Consulting", "CONSULTING", "A"),
    ("", "500.00", "500.00", "EUR", "2024-03-01", "2024-03-01", "INV3", "", "6000", "Freight costs", "Logistics", "A"),
    ("Atlas Freight", "-300.00", "-300.00", "EUR", "2024-03-05", "2024-03-05", "INV4", "", "6000", "Freight costs", "Logistics", "A"),
    # exact duplicate of the first row
    ("Atlas Freight", "1000.00", "1000.00", "EUR", "2024-01-15", "2024-01-15", "INV1", "PO1", "6000", "Freight costs", "Logistics", "A"),
    # same document number, different amount
    ("Sopra Steria", "750.00", "750.00", "EUR", "2024-02-11", "2024-02-11", "INV2", "", "6200", "Consulting", "IT Services", "A"),
    # no currency, but a group amount to fall back on
    ("Nordwind Papier", "400.00", "400.00", "", "2024-04-01", "2024-04-01", "INV5", "", "6300", "Office supplies", "Facility", "B"),
    ("Nordwind Papier", "600.00", "600.00", "EUR", "2099-01-01", "2099-01-01", "INV6", "", "6300", "Office supplies", "Facility", "B"),
    ("Delta Env", "800.00", "800.00", "EUR", "2024-01-01", "2024-02-01", "INV7", "", "6400", "Miscellaneous", "", "B"),
    # no amount at all
    ("Delta Env", "", "", "EUR", "2024-05-01", "2024-05-01", "INV8", "", "6400", "Other expenses", "", "B"),
    # aggregate with a marker, and no identifiers
    ("*** SUBTOTAL ***", "3950.00", "3950.00", "EUR", "", "", "", "", "", "", "", "A"),
    # aggregate recognisable only by having an amount and no identifiers
    ("", "1800.00", "1800.00", "EUR", "", "", "", "", "", "", "", "B"),
]

_COLUMN_ORDER = (
    "supplier", "amount_local", "amount_group", "currency", "posting_date",
    "document_date", "invoice_number", "purchase_order", "gl_account",
    "gl_description", "category", "company",
)


def defective_table() -> pd.DataFrame:
    """A canonical table carrying one of every defect, for the quality checks."""
    from transform.canonical_table import BASE_COLUMNS

    records = []
    for index, row in enumerate(_DEFECTIVE_ROWS):
        # Start from every canonical column so the fixture keeps working as the
        # schema grows; the ones this fixture cares about are filled below.
        record = {column: "" for column in BASE_COLUMNS}
        record.update(zip(_COLUMN_ORDER, row))
        record.update(
            dataset_id="01_export",
            source_file="export.csv",
            source_sheet="",
            source_row=str(index + 2),
            company_name=f"Company {record['company']}",
            supplier_id="",
            cost_center="",
            profit_center="",
        )
        records.append(record)
    return pd.DataFrame(records, dtype=str)[list(BASE_COLUMNS)]


@pytest.fixture
def defective_run(run_root):
    """A run whose canonical table is the defective one above."""
    from core.run import create_run
    from core.table import write_table

    run_id = create_run().run_id
    write_table(run_id, defective_table(), "canonical_table")
    return run_id


@pytest.fixture
def lever_run(defective_run):
    """A run taken through profiling and the rule engine, then given lever data.

    Going the real route means the profiling and rule-engine artifacts exist, so
    anything reading them -- the summary, the chat context -- sees a realistic run
    rather than a table that appeared from nowhere.
    """
    from core.table import load_table, write_table
    from profiling.data_profiling import confirm_profiling, run_profiling
    from transform.rule_engine import run_rule_engine

    run_profiling(defective_run)
    confirm_profiling(defective_run)
    run_rule_engine(defective_run)

    table = load_table(defective_run)
    table["amount_eur"] = [1000.0, 1000.0, 500.0, 500.0, 20.0, 20.0, 300.0, 300.0, 80.0, 80.0, 0.0, 0.0]
    table["include_addressable_spend"] = [True] * 10 + [False, False]
    table["supplier_normalized"] = (
        ["Atlas"] * 2 + ["Sopra"] * 2 + ["Tiny"] * 2 + ["Delta"] * 2 + ["Vega"] * 2 + ["", ""]
    )
    table["company_name"] = ["Alpha", "Beta"] * 6
    table["supplier_contract_status"] = (
        ["yes"] * 2 + ["no"] * 2 + ["unknown"] * 2 + ["no"] * 2 + ["yes"] * 2 + ["", ""]
    )
    table["purchase_order"] = ["PO"] * 4 + [""] * 2 + ["PO"] * 6
    table["include_spend_analysis"] = [True] * 10 + [False, False]
    table["include_supplier_analysis"] = [True] * 10 + [False, False]
    write_table(defective_run, table, "spend_classification")

    from agents.lever_reasoning import LeverReasoningProposal
    from levers.engine import run_levers

    run_levers(
        defective_run,
        client=FakeClient(
            LeverReasoningProposal(
                levers=[], priority_rationale="", recommended_order=[], order_reason=""
            )
        ),
    )
    return defective_run
