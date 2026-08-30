"""Writing an export into the run it came from.

A report that has been handed to someone should still be findable next to the
evidence for it, so both files are kept as the artifacts of a stage like every
other output. They are built on request rather than on every run: nobody needs a
nine megabyte workbook they did not ask for.
"""

import re

import pandas as pd

from analysis.report import ReportDocument, build_report
from core.run import get_logger, record_step, step_path
from core.table import has_table, load_table
from export.excel import build_workbook
from export.html import build_html

STEP = "export"
WORKBOOK_NAME = "report.xlsx"
PAGE_NAME = "report.html"


def build_exports(run_id: str) -> tuple[bytes, str]:
    """Both files, written into the run and returned for download."""
    logger = get_logger(run_id)
    document = build_report(run_id)
    table = load_table(run_id) if has_table(run_id) else pd.DataFrame()

    workbook = build_workbook(document, table)
    page = build_html(document)

    target = step_path(run_id, STEP)
    (target / WORKBOOK_NAME).write_bytes(workbook)
    (target / PAGE_NAME).write_text(page, encoding="utf-8")
    record_step(run_id, STEP, [target / WORKBOOK_NAME, target / PAGE_NAME])

    logger.info(
        "exports written: workbook %.1f MB, page %.1f MB",
        len(workbook) / 1e6,
        len(page.encode("utf-8")) / 1e6,
    )
    return workbook, page


def file_stem(document: ReportDocument) -> str:
    """A name a recipient can read: the group and the day it was prepared."""
    group = re.sub(r"[^A-Za-z0-9]+", "_", document.cover.group).strip("_").lower()
    return f"procurement_levers_{group or 'analysis'}_{document.cover.prepared_on:%Y%m%d}"


def has_exports(run_id: str) -> bool:
    return (step_path(run_id, STEP) / WORKBOOK_NAME).is_file()
