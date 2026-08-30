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
# Above this share of rows, the category is determined by the GL description and
# therefore renames the accounting classification instead of adding a procurement
# one. Category analysis is switched off.
CATEGORY_DEPENDENCY_RATIO = 0.9
# Findings quote a handful of source rows so a reviewer can look them up.
MAX_FINDING_EXAMPLES = 5


def runs_dir() -> Path:
    return Path(os.getenv("PLI_RUNS_DIR", "runs")).expanduser()


# ECB daily reference rates, shipped with the repository so conversion needs no
# network at runtime and a run is reproducible years later. Refreshed from the
# URL below by fx.ecb.fetch_ecb_history().
ECB_RATES_FILE = Path(
    os.getenv("PLI_ECB_RATES", Path(__file__).resolve().parent.parent / "ecb_fx_reference_rates.csv")
)
ECB_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"

# Supplier name similarity thresholds: at or above AUTO the names merge without
# asking anyone; between FLOOR and AUTO the matching agent judges; below FLOOR
# the pair is not a candidate at all.
SUPPLIER_AUTO_MERGE = 0.95
SUPPLIER_CANDIDATE_FLOOR = 0.70

# A supplier name this close to one of the group's own company names is the group
# buying from itself.
INTERCOMPANY_MATCH = 0.85
# Share of company names a token must appear in to count as the group's own name.
INTERCOMPANY_STEM_SHARE = 0.5
