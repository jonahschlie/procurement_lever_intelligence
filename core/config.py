"""Paths and constants for the application.

The runs directory is resolved per call rather than at import time so that the
deployment target can point it elsewhere via ``PLI_RUNS_DIR`` -- on Streamlit
Community Cloud the working directory is ephemeral.
"""

import os
from pathlib import Path

ALLOWED_EXTENSIONS = ("csv", "xlsx")
PREVIEW_ROWS = 20


def runs_dir() -> Path:
    return Path(os.getenv("PLI_RUNS_DIR", "runs")).expanduser()
