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

# A row whose amount restates the block around it is a subtotal even when it kept
# its posting date and document number. The tolerance absorbs rounding in the
# source; the minimum block size stops a handful of similar bookings from
# nominating each other.
AGGREGATE_SUM_TOLERANCE = 0.005
AGGREGATE_SUM_MIN_ROWS = 4


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

# --- AI cost --------------------------------------------------------------
# List prices in USD per 1 million tokens, as (input, output).
#
# NOT VERIFIED. Taken as a working assumption on 2026-08-30 so the sidebar has
# something to multiply with -- look them up and correct them here. The figures
# shown next to the euros are the token counts themselves, which are measured, so
# a wrong price here makes the euro column wrong and nothing else.
#
# Matched by longest prefix: the API answers with a dated name such as
# "gpt-5-mini-2025-08-07", and pinning the exact build here would silently drop to
# zero the next time the model is rolled forward.
TOKEN_PRICES_USD = {
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5": (1.25, 10.00),
}

# What a run may spend before the sidebar starts warning. A warning, not a gate:
# the price of a call is only known after it was made.
DEFAULT_BUDGET_EUR = 5.0


# --- Levers ---------------------------------------------------------------
# Saving rates are ASSUMPTIONS, not derived from any dataset. They live here so
# they can be replaced with a firm's own benchmarks without touching logic, and
# the UI shows every rate next to the figure it produced.
LEVER_RATES = {
    # Recovery rather than negotiation, so the rates are higher: the money was
    # already paid and is claimed back.
    "duplicate_payments": (0.20, 0.40, 0.60),
    "supplier_consolidation": (0.02, 0.04, 0.07),
    "contract_coverage": (0.01, 0.03, 0.05),
    "maverick": (0.02, 0.05, 0.10),
    "tail_spend": (0.05, 0.10, 0.15),
}

# Which lever claims a euro when several apply. Ordered by how specific the
# population is -- a property of the data rather than of the assumed rates.
# Ordering by rate instead would maximise the total and bias it optimistic.
LEVER_PRECEDENCE = (
    "duplicate_payments",
    "tail_spend",
    "maverick",
    "contract_coverage",
    "supplier_consolidation",
)

# A supplier above this share of addressable spend is a dependency worth naming.
SUPPLIER_DEPENDENCY_THRESHOLD = 0.10

# A supplier below this share of addressable spend counts as tail.
TAIL_SPEND_THRESHOLD = 0.01

# Effort rubric: how many suppliers and companies have to be coordinated.
EFFORT_SUPPLIERS = (10, 30)
EFFORT_COMPANIES = (2, 5)
