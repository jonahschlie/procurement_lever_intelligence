"""Paths and constants for the application.

The runs directory is resolved per call rather than at import time so that the
deployment target can point it elsewhere via ``PLI_RUNS_DIR`` -- on Streamlit
Community Cloud the working directory is ephemeral.
"""

import os
from pathlib import Path

ALLOWED_EXTENSIONS = ("csv", "xlsx")
PREVIEW_ROWS = 20

# Raw rows shown next to a proposed mapping so the user can check it against reality.
RAW_PREVIEW_ROWS = 8

# Mappings below this confidence are flagged for review. Everything stays editable
# regardless -- the threshold only decides what gets someone's attention first.
CONFIDENCE_THRESHOLD = 0.7

# What the schema mapping agent is shown per column. Deliberately small: these are
# real client values leaving the machine.
MAX_SAMPLE_VALUES = 5
MAX_SAMPLE_LENGTH = 60


def runs_dir() -> Path:
    return Path(os.getenv("PLI_RUNS_DIR", "runs")).expanduser()
