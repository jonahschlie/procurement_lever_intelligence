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

# A required field missing beyond this share is a high-severity finding.
MISSING_HIGH_RATIO = 0.05
# Above this share of rows, a category column that merely repeats the GL text is
# treated as accounting classification and category analysis is switched off.
CATEGORY_EQUALS_GL_RATIO = 0.8
# Findings quote a handful of source rows so a reviewer can look them up.
MAX_FINDING_EXAMPLES = 5


def runs_dir() -> Path:
    return Path(os.getenv("PLI_RUNS_DIR", "runs")).expanduser()
