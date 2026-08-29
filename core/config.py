"""Paths and constants for the application.

The data directory is resolved per call rather than at import time so that the
deployment target can point it elsewhere via ``PLI_DATA_DIR`` -- on Streamlit
Community Cloud the working directory is ephemeral.
"""

import os
from pathlib import Path

ALLOWED_EXTENSIONS = ("csv", "xlsx")
PREVIEW_ROWS = 20


def data_dir() -> Path:
    return Path(os.getenv("PLI_DATA_DIR", "data")).expanduser()


def uploads_dir() -> Path:
    return data_dir() / "uploads"
