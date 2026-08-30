"""The charts, as pure functions from data to an Altair specification.

Kept free of Streamlit so the Excel export can render the same figures later
without a second implementation. Every function returns both the chart and the
table behind it, because the guideline requires a table view wherever a hue sits
below the contrast floor -- and because a reader should always be able to check
the picture against the numbers.
"""

from dataclasses import dataclass

import altair as alt
import pandas as pd

from core import palette

CHART_HEIGHT = 320


@dataclass(frozen=True)
class Figure:
    """A chart and the rows it was drawn from."""

    chart: alt.Chart
    data: pd.DataFrame
    caption: str = ""


def _figure(chart, frame: pd.DataFrame, caption: str = "") -> Figure:
    return Figure(chart=chart, data=frame, caption=caption)


def _base(chart: alt.Chart) -> alt.Chart:
    """Recessive chrome: the marks carry the meaning, not the grid."""
    return chart.configure_view(strokeWidth=0).configure_axis(
        grid=True,
        gridColor=palette.GRID,
        domainColor=palette.GRID,
        tickColor=palette.GRID,
        labelColor=palette.TEXT_SECONDARY,
        titleColor=palette.TEXT_SECONDARY,
        labelFontSize=11,
        titleFontSize=11,
    ).configure_legend(
        labelColor=palette.TEXT_SECONDARY, titleColor=palette.TEXT_SECONDARY
    )


def spend_waterfall(chain: list[dict]) -> Figure:
    """Gross spend down to what procurement can negotiate.

    Each step starts where the previous one ended, so the bars read as one
    descent rather than as unrelated totals.
    """
    rows, running = [], 0.0
    for index, step in enumerate(chain):
        deduction = step.get("delta") is not None
        if deduction:
            start, end = running, running + step["delta"]
        else:
            start, end = 0.0, step["amount"]
        running = end if deduction else step["amount"]
        rows.append(
            {
                "order": index,
                "step": step["label"],
                "start": start,
                "end": end,
                "amount": step["amount"],
                "role": "deduction" if deduction else ("result" if index == len(chain) - 1 else "total"),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return _figure(alt.Chart(pd.DataFrame({"x": []})).mark_bar(), frame)

    order = list(frame["step"])
    colours = {
        "total": palette.WATERFALL_TOTAL,
        "deduction": palette.WATERFALL_DEDUCTION,
        "result": palette.WATERFALL_RESULT,
    }
    encoding = {
        "x": alt.X("step:N", sort=order, title=None, axis=alt.Axis(labelAngle=-30)),
        "y": alt.Y("start:Q", title="EUR", axis=alt.Axis(format="~s")),
        "y2": alt.Y2("end:Q"),
    }
    bars = (
        alt.Chart(frame)
        .mark_bar(cornerRadius=4, size=44)
        .encode(
            **encoding,
            color=alt.Color(
                "role:N",
                scale=alt.Scale(domain=list(colours), range=list(colours.values())),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("step:N", title="Step"),
                alt.Tooltip("amount:Q", title="EUR", format=",.0f"),
            ],
        )
    )
    # Direct labels, which is the relief the contrast warning requires.
    labels = (
        alt.Chart(frame)
        .mark_text(dy=-8, fontSize=11, color=palette.TEXT_PRIMARY)
        .encode(
            x=alt.X("step:N", sort=order),
            y=alt.Y("end:Q"),
            text=alt.Text("amount:Q", format=",.0f"),
        )
    )
    chart = (bars + labels).properties(height=CHART_HEIGHT)
    return _figure(_base(chart), frame)


def supplier_share(spend: pd.Series) -> Figure:
    """Part-to-whole at a glance: the largest five and everything else.

    A pie stays legible only up to six segments, so the tail is folded rather
    than sliced into shares nobody can compare.
    """
    ranked = spend.sort_values(ascending=False)
    top = ranked.head(palette.PIE_SLICES)
    rest = ranked[palette.PIE_SLICES :]

    rows = [{"supplier": name, "spend": float(value)} for name, value in top.items()]
    if not rest.empty:
        rows.append({"supplier": f"Other ({len(rest)} suppliers)", "spend": float(rest.sum())})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return _figure(alt.Chart(pd.DataFrame({"x": []})).mark_arc(), frame)

    total = frame["spend"].sum() or 1
    frame["share"] = frame["spend"] / total

    colours = palette.categorical(min(len(top), len(palette.CATEGORICAL)))
    if not rest.empty:
        colours = colours + [palette.OTHER]

    chart = (
        alt.Chart(frame)
        .mark_arc(innerRadius=70, stroke=palette.SURFACE, strokeWidth=2)
        .encode(
            theta=alt.Theta("spend:Q", stack=True),
            color=alt.Color(
                "supplier:N",
                sort=list(frame["supplier"]),
                scale=alt.Scale(domain=list(frame["supplier"]), range=colours),
                legend=alt.Legend(title=None, orient="right"),
            ),
            tooltip=[
                alt.Tooltip("supplier:N", title="Supplier"),
                alt.Tooltip("spend:Q", title="EUR", format=",.0f"),
                alt.Tooltip("share:Q", title="Share", format=".1%"),
            ],
        )
        .properties(height=CHART_HEIGHT)
    )
    return _figure(_base(chart), frame)


def supplier_ranking(spend: pd.Series, limit: int = 15) -> Figure:
    """The full ranking with cumulative share -- what the pie cannot show."""
    ranked = spend.sort_values(ascending=False)
    total = ranked.sum() or 1
    frame = pd.DataFrame(
        {
            "supplier": ranked.index.astype(str),
            "spend": ranked.to_numpy(dtype=float),
        }
    )
    frame["share"] = frame["spend"] / total
    frame["cumulative"] = frame["share"].cumsum()
    shown = frame.head(limit)
    if shown.empty:
        return _figure(alt.Chart(pd.DataFrame({"x": []})).mark_bar(), frame)

    bars = (
        alt.Chart(shown)
        .mark_bar(cornerRadius=4, color=palette.BRAND, height=16)
        .encode(
            y=alt.Y("supplier:N", sort="-x", title=None),
            x=alt.X("spend:Q", title="EUR", axis=alt.Axis(format="~s")),
            tooltip=[
                alt.Tooltip("supplier:N", title="Supplier"),
                alt.Tooltip("spend:Q", title="EUR", format=",.0f"),
                alt.Tooltip("cumulative:Q", title="Cumulative", format=".1%"),
            ],
        )
    )
    labels = (
        alt.Chart(shown)
        .mark_text(align="left", dx=4, fontSize=11, color=palette.TEXT_SECONDARY)
        .encode(
            y=alt.Y("supplier:N", sort="-x"),
            x=alt.X("spend:Q"),
            text=alt.Text("share:Q", format=".1%"),
        )
    )
    chart = (bars + labels).properties(height=max(CHART_HEIGHT, 22 * len(shown)))
    return _figure(_base(chart), frame)


def monthly_spend(frame: pd.DataFrame) -> Figure:
    """One line, one hue: the shape over time is the point, not the categories."""
    if frame.empty:
        return _figure(alt.Chart(pd.DataFrame({"x": []})).mark_line(), frame)

    chart = (
        alt.Chart(frame)
        .mark_line(strokeWidth=2, color=palette.BRAND, point=alt.OverlayMarkDef(size=60))
        .encode(
            x=alt.X("month:N", title=None, axis=alt.Axis(labelAngle=-30)),
            y=alt.Y("spend:Q", title="EUR", axis=alt.Axis(format="~s")),
            tooltip=[
                alt.Tooltip("month:N", title="Month"),
                alt.Tooltip("spend:Q", title="EUR", format=",.0f"),
            ],
        )
        .properties(height=CHART_HEIGHT)
    )
    return _figure(_base(chart), frame)


def contract_coverage(frame: pd.DataFrame) -> Figure:
    """Where contract cover is thin, company by company."""
    if frame.empty:
        return _figure(alt.Chart(pd.DataFrame({"x": []})).mark_bar(), frame)

    order = ["None on file", "On file", "Not in the master"]
    chart = (
        alt.Chart(frame)
        .mark_bar(cornerRadius=3, height=18, stroke=palette.SURFACE, strokeWidth=2)
        .encode(
            y=alt.Y("company:N", sort="-x", title=None),
            x=alt.X("spend:Q", stack="normalize", title="Share of spend", axis=alt.Axis(format="%")),
            color=alt.Color(
                "status:N",
                scale=alt.Scale(domain=order, range=[palette.CATEGORICAL[1], palette.CATEGORICAL[2], palette.OTHER]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=[
                alt.Tooltip("company:N", title="Company"),
                alt.Tooltip("status:N", title="Contract"),
                alt.Tooltip("spend:Q", title="EUR", format=",.0f"),
            ],
        )
        .properties(height=max(CHART_HEIGHT, 26 * frame["company"].nunique()))
    )
    return _figure(_base(chart), frame)


def lever_allocation(frame: pd.DataFrame) -> Figure:
    """How the addressable spend divides across levers -- each euro once."""
    if frame.empty:
        return _figure(alt.Chart(pd.DataFrame({"x": []})).mark_bar(), frame)

    order = list(frame["lever"])
    chart = (
        alt.Chart(frame)
        .mark_bar(cornerRadius=4, size=48, stroke=palette.SURFACE, strokeWidth=2)
        .encode(
            x=alt.X("spend:Q", stack=True, title="EUR", axis=alt.Axis(format="~s")),
            color=alt.Color(
                "lever:N",
                sort=order,
                scale=alt.Scale(domain=order, range=palette.categorical(len(order))),
                legend=alt.Legend(title=None, orient="bottom", columns=2),
            ),
            tooltip=[
                alt.Tooltip("lever:N", title="Lever"),
                alt.Tooltip("spend:Q", title="EUR", format=",.0f"),
            ],
        )
        .properties(height=120)
    )
    return _figure(_base(chart), frame)
