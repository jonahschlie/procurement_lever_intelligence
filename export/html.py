"""The analysis as one HTML file, for the reader who only wants to read it.

Self-contained on purpose. A report mailed to a portfolio company is opened from a
download folder, on a train, behind a corporate proxy -- so the Vega runtime is
embedded rather than fetched, and the file works with the network switched off.

The runtime is written once and every chart is only its specification after that.
Six charts each carrying their own copy would be nine megabytes; shared, the file
is about one and a half.

Same colours, same order and same figures as the Executive Summary, because both
render analysis.report.
"""

import json
import re
from dataclasses import dataclass

import pandas as pd
from jinja2 import Environment, StrictUndefined

from analysis.report import ReportDocument
from core import palette

# Altair states the Vega-Lite version it wrote in the spec; vl-convert ships a
# fixed set of runtimes. Reading it beats hardcoding, which would break silently
# the next time Altair moves.
_SCHEMA = re.compile(r"/vega-lite/v(\d+)\.(\d+)")

CHART_WIDTH = 720
AMOUNT_SUFFIXES = ("(EUR)", "(local)")

# A chart specification carries values from the data -- supplier names, company
# names, GL text. Inside a <script> element the browser looks for "</script>"
# before it looks for JSON, so a supplier called "</script>..." would end the
# block and run whatever came next. Escaping these as \u sequences keeps the JSON
# valid and makes the sequence unwritable. U+2028 and U+2029 are line terminators
# in JavaScript but not in JSON, which is the same problem from the other side.
_SCRIPT_SAFE = {
    "<": "\\u003c",
    ">": "\\u003e",
    "&": "\\u0026",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}


@dataclass(frozen=True)
class Rendered:
    """A figure ready for the page: a spec to embed, or an SVG if that failed."""

    element_id: str
    caption: str
    spec: str | None
    svg: str | None
    table: str


def build_html(document: ReportDocument) -> str:
    """The whole report as one string. No request leaves the page when it opens."""
    runtime, degraded = _runtime()
    sections = [
        {
            "id": _slug(section.title),
            "title": section.title,
            "blocks": [_block(block) for block in section.blocks],
        }
        for section in document.sections
    ]
    return _environment().from_string(TEMPLATE).render(
        cover=document.cover,
        sections=sections,
        css=_css(),
        runtime=runtime,
        degraded=degraded,
        specs=[
            figure
            for section in sections
            for block in section["blocks"]
            for figure in block["figures"]
            if figure.spec
        ],
    )


# --- rendering the parts ---------------------------------------------------


def _runtime() -> tuple[str, bool]:
    """The Vega bundle, once. Returns the script and whether charts are static."""
    try:
        import vl_convert as vlc

        return vlc.javascript_bundle(vl_version=_vl_version()), False
    except Exception:
        # Said out loud in the report rather than quietly delivering something else.
        return "", True


def _vl_version() -> str:
    import altair as alt
    import vl_convert as vlc

    supported = vlc.get_vegalite_versions()
    match = _SCHEMA.search(alt.SCHEMA_URL)
    if match:
        wanted = f"{match.group(1)}.{match.group(2)}"
        if wanted in supported:
            return wanted
        same_major = [v for v in supported if v.split(".")[0] == match.group(1)]
        if same_major:
            return same_major[-1]
    return supported[-1]


def _block(block) -> dict:
    return {
        "title": block.title,
        "caption": block.caption,
        "metrics": block.metrics,
        "body": _paragraphs(block.body),
        "table": _table(block.table),
        "figures": [
            _figure(figure, f"{_slug(block.title)}-{index}")
            for index, figure in enumerate(block.figures)
        ],
    }


def _figure(figure, element_id: str) -> Rendered:
    spec = figure.chart.to_dict()
    # An explicit width means Vega never has to measure a container, which is
    # what makes a chart inside an inactive tab render at its full size.
    spec.setdefault("width", CHART_WIDTH)

    try:
        import vl_convert as vlc

        vlc.get_vegalite_versions()  # the import alone does not prove it works
        return Rendered(
            element_id=element_id,
            caption=figure.caption,
            spec=_script_json(spec),
            svg=None,
            table=_table(figure.data),
        )
    except Exception:
        return Rendered(
            element_id=element_id,
            caption=figure.caption,
            spec=None,
            svg=_svg(spec),
            table=_table(figure.data),
        )


def _script_json(spec: dict) -> str:
    """JSON that cannot break out of the <script> element it is written into."""
    text = json.dumps(spec)
    for character, escape in _SCRIPT_SAFE.items():
        text = text.replace(character, escape)
    return text


def _svg(spec: dict) -> str:
    try:
        import vl_convert as vlc

        return vlc.vegalite_to_svg(spec)
    except Exception:
        return ""


def _table(frame: pd.DataFrame | None) -> str:
    if frame is None or frame.empty:
        return ""
    shown = frame.copy()
    for column in shown.columns:
        if str(column).endswith(AMOUNT_SUFFIXES) or column in ("spend", "amount", "EUR"):
            shown[column] = pd.to_numeric(shown[column], errors="coerce").map(
                lambda value: "" if pd.isna(value) else f"{value:,.0f}"
            )
        elif column in ("share", "cumulative"):
            shown[column] = pd.to_numeric(shown[column], errors="coerce").map(
                lambda value: "" if pd.isna(value) else f"{value:.1%}"
            )
    return shown.to_html(index=False, border=0, escape=True, na_rep="")


def _paragraphs(body: str) -> list[str]:
    return [line.lstrip("- ").strip() for line in body.splitlines() if line.strip()]


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "block"


def _environment() -> Environment:
    """Autoescaping on: supplier names and agent text reach the page as text.

    The few fragments that are genuinely markup -- the stylesheet, the runtime,
    the tables pandas rendered, the chart specifications -- pass through Jinja's
    own `safe`, which is the only thing that marks a string as already-safe.
    """
    return Environment(autoescape=True, undefined=StrictUndefined)


def _css() -> str:
    """Every colour from core.palette, so the file and the app cannot disagree."""
    series = "\n".join(
        f"  --series-{index}: {colour};"
        for index, colour in enumerate(palette.CATEGORICAL, start=1)
    )
    return f""":root {{
  --brand: {palette.BRAND};
  --surface: {palette.SURFACE};
  --band: {palette.SEQUENTIAL[0]};
  --ink: {palette.TEXT_PRIMARY};
  --muted: {palette.TEXT_SECONDARY};
  --grid: {palette.GRID};
{series}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--surface); color: var(--ink);
  font: 15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}}
header {{ background: var(--brand); color: #fff; padding: 28px 40px; }}
header h1 {{ margin: 0 0 4px; font-size: 26px; font-weight: 600; }}
header .group {{ font-size: 18px; opacity: .85; }}
header .facts {{ margin-top: 18px; display: flex; flex-wrap: wrap; gap: 34px; }}
header .facts div span {{ display: block; font-size: 12px; opacity: .75; }}
header .facts div strong {{ font-size: 19px; font-weight: 600; }}
main {{ max-width: 1080px; margin: 0 auto; padding: 0 40px 64px; }}
.tabs {{ display: flex; gap: 4px; border-bottom: 2px solid var(--grid); margin: 0 0 28px; }}
.tabs label {{
  padding: 12px 18px; cursor: pointer; color: var(--muted);
  border-bottom: 2px solid transparent; margin-bottom: -2px; font-weight: 500;
}}
input[name="tab"] {{ display: none; }}
.panel {{ display: none; }}
h2 {{ font-size: 19px; margin: 34px 0 2px; color: var(--brand); }}
h2:first-child {{ margin-top: 0; }}
.caption {{ color: var(--muted); font-size: 13px; margin: 0 0 14px; }}
.metrics {{ display: flex; flex-wrap: wrap; gap: 34px; margin: 14px 0 18px; }}
.metrics div span {{ display: block; font-size: 12px; color: var(--muted); }}
.metrics div strong {{ font-size: 20px; font-weight: 600; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin: 10px 0 18px; }}
th {{
  background: var(--brand); color: #fff; text-align: left;
  padding: 8px 10px; font-weight: 600;
}}
td {{ padding: 7px 10px; border-bottom: 1px solid var(--grid); }}
tr:nth-child(even) td {{ background: var(--band); }}
details {{ margin: 6px 0 22px; }}
summary {{ cursor: pointer; color: var(--muted); font-size: 13px; }}
.chart {{ margin: 10px 0 4px; }}
.note {{
  background: var(--band); border-left: 4px solid var(--brand);
  padding: 14px 18px; margin: 26px 0; font-size: 13px;
}}
footer {{ color: var(--muted); font-size: 12px; padding: 0 40px 40px; max-width: 1080px; margin: 0 auto; }}
"""


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{{ cover.title }} — {{ cover.group }}</title>
<style>{{ css | safe }}</style>
</head><body>

<header>
  <h1>{{ cover.title }}</h1>
  <div class="group">{{ cover.group }}</div>
  <div class="facts">
    {% for label, value in cover.metrics %}
    <div><span>{{ label }}</span><strong>{{ value }}</strong></div>
    {% endfor %}
    <div><span>Rows analysed</span><strong>{{ "{:,}".format(cover.rows_analysed) }}</strong></div>
  </div>
</header>

<main>
  {% for section in sections %}
  <input type="radio" name="tab" id="tab-{{ section.id }}"{% if loop.first %} checked{% endif %}>
  {% endfor %}
  <div class="tabs">
    {% for section in sections %}
    <label for="tab-{{ section.id }}">{{ section.title }}</label>
    {% endfor %}
  </div>

  {% for section in sections %}
  <div class="panel" id="panel-{{ section.id }}">
    {% for block in section.blocks %}
    <h2>{{ block.title }}</h2>
    {% if block.caption %}<p class="caption">{{ block.caption }}</p>{% endif %}
    {% if block.metrics %}
    <div class="metrics">
      {% for label, value in block.metrics %}
      <div><span>{{ label }}</span><strong>{{ value }}</strong></div>
      {% endfor %}
    </div>
    {% endif %}
    {% for line in block.body %}<p>{{ line }}</p>{% endfor %}
    {% if block.table %}{{ block.table | safe }}{% endif %}
    {% for figure in block.figures %}
      {% if figure.caption %}<p class="caption">{{ figure.caption }}</p>{% endif %}
      {% if figure.spec %}
      <div class="chart" id="{{ figure.element_id }}"></div>
      {% elif figure.svg %}
      <div class="chart">{{ figure.svg | safe }}</div>
      {% endif %}
      {% if figure.table %}
      <details><summary>Show data</summary>{{ figure.table | safe }}</details>
      {% endif %}
    {% endfor %}
    {% endfor %}
  </div>
  {% endfor %}

  <div class="note">{{ cover.note }}</div>
  {% if degraded %}
  <div class="note">The interactive chart runtime could not be embedded, so the
  figures in this file are static images. The numbers are unaffected and every
  chart still carries its table.</div>
  {% endif %}
</main>

<footer>
  Run {{ cover.run_id }} · prepared {{ cover.prepared_on }} ·
  {{ "{:,}".format(cover.rows_total) }} rows from {{ cover.sources | join(", ") or "a submitted table" }}
</footer>

<style>
{% for section in sections %}
#tab-{{ section.id }}:checked ~ .tabs label[for="tab-{{ section.id }}"] {
  color: var(--brand); border-bottom-color: var(--brand);
}
#tab-{{ section.id }}:checked ~ #panel-{{ section.id }} { display: block; }
{% endfor %}
</style>

{% if runtime %}
<script>{{ runtime | safe }}</script>
<script>
const SPECS = {
{% for figure in specs %}  "{{ figure.element_id }}": {{ figure.spec | safe }}{{ "," if not loop.last }}
{% endfor %}};
for (const [id, spec] of Object.entries(SPECS)) {
  vegaEmbed("#" + id, spec, {actions: false, renderer: "svg"});
}
</script>
{% endif %}
</body></html>
"""
