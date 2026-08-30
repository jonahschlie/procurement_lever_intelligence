"""Chart colours, chosen by running the validator rather than by eye.

The categorical set below passed every hard check of the visualisation guideline
on the light surface:

    lightness band    PASS  all six inside 0.43-0.77
    chroma floor      PASS
    CVD separation    PASS  worst adjacent dE 9.1 (protan), tritan 22.3
    normal vision     PASS  worst adjacent dE 22.9
    contrast          WARN  two hues below 3:1

The contrast warning is not dismissable: the guideline requires visible labels or
a table view in exchange, so every chart carries direct labels and a "Show data"
expander.

The brand purple is deliberately **not** among the series colours. Measured, it
sits at lightness 0.301 -- outside the permitted band -- and failed three checks
as a categorical hue. It remains the interface accent and the dark end of the
sequential ramp, which is what it is good at.

Known limit: on a dark surface two of these hues fall out of the lightness band.
The application declares a light theme, so this is recorded rather than hidden.
"""

# The interface accent, from .streamlit/config.toml.
BRAND = "#431356"

# Fixed order, never cycled. A seventh series folds into "Other".
CATEGORICAL = (
    "#8A4FB5",  # violet, brand-adjacent
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#2a78d6",  # blue
    "#e34948",  # red
)

# One hue, light to dark, for magnitude. Ends on the brand purple.
SEQUENTIAL = ("#EDE4F2", "#C9AED8", "#A578BE", "#7B42A4", "#431356")

# Waterfall roles. Deliberately no red/green: a deduction is a removal from a
# population, not an error, and colouring it as failure would misread the chart.
WATERFALL_TOTAL = BRAND
WATERFALL_DEDUCTION = "#B9A6C4"
WATERFALL_RESULT = "#8A4FB5"

# Chart chrome, kept recessive so the marks carry the meaning.
SURFACE = "#FFFFFF"
TEXT_PRIMARY = "#1F1B23"
TEXT_SECONDARY = "#6B6472"
GRID = "#E8E3EC"
OTHER = "#9E97A6"

# A pie is legible only at a glance and only up to six segments, so the ranking
# is cut here and the remainder folded into "Other".
PIE_SLICES = 5


def categorical(count: int) -> list[str]:
    """The first `count` series colours, in the fixed order."""
    if count > len(CATEGORICAL):
        raise ValueError(
            f"{count} series exceeds the {len(CATEGORICAL)} validated hues; "
            "fold the tail into 'Other' or facet instead of generating a colour"
        )
    return list(CATEGORICAL[:count])
