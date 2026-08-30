"""The HTML report: one file, no network, and the dashboard's own figures."""

import json
import re
from html.parser import HTMLParser

import pandas as pd
import pytest

from agents.sme_questions import SmeQuestion, SmeQuestionProposal
from analysis.report import build_report
from analysis.summary import build_summary
from core import palette
from export.html import build_html
from tests.conftest import FakeClient

EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*['"](https?://[^'"]+)""")


class _Wellformed(HTMLParser):
    """Enough of a parser to notice a template that produced broken markup."""

    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.void = {"meta", "br", "hr", "img", "input", "link"}

    def handle_starttag(self, tag, attrs):
        if tag not in self.void:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass


@pytest.fixture
def page(lever_run):
    build_summary(
        lever_run,
        client=FakeClient(
            SmeQuestionProposal(
                questions=[
                    SmeQuestion(
                        question="Is the missing purchase order a policy choice?",
                        rationale="Most bookings carry none.",
                        addressee="procurement",
                        unlocks="Whether maverick spend is a gap or the norm.",
                    )
                ]
            )
        ),
    )
    return build_html(build_report(lever_run)), lever_run


def test_the_file_fetches_nothing_when_it_opens(page):
    html, _ = page

    assert EXTERNAL.findall(html) == []
    assert "cdn.jsdelivr.net" not in html
    # The runtime is in the file, which is the point of it being large.
    assert "vegaEmbed" in html


def test_the_markup_is_wellformed(page):
    html, _ = page
    parser = _Wellformed()
    parser.feed(html)

    assert parser.stack == [], f"unclosed tags: {parser.stack}"


def test_every_section_of_the_dashboard_becomes_a_tab(page):
    html, run_id = page
    titles = [section.title for section in build_report(run_id).sections]

    for title in titles:
        assert f">{title}</label>" in html
    assert titles


def test_each_chart_carries_a_specification_and_its_numbers(page):
    html, _ = page

    specs = json.loads("{" + re.search(r"const SPECS = \{(.*)\n\};", html, re.S).group(1) + "}")
    divs = set(re.findall(r'class="chart" id="([^"]+)"', html))

    assert specs
    assert set(specs) == divs
    # The guideline asks for a table wherever a hue sits below the contrast floor.
    assert html.count("<summary>Show data</summary>") == len(specs)


def test_charts_carry_an_explicit_width_so_a_hidden_tab_still_draws(page):
    html, _ = page
    specs = json.loads("{" + re.search(r"const SPECS = \{(.*)\n\};", html, re.S).group(1) + "}")

    assert all(spec.get("width") for spec in specs.values())


def test_the_colours_are_the_applications_own(page):
    html, _ = page

    assert f"--brand: {palette.BRAND};" in html
    for colour in palette.CATEGORICAL:
        assert colour in html


def test_the_assumption_note_travels_with_the_numbers(page):
    html, _ = page

    assert "assumptions, not findings" in html


def test_text_from_the_data_is_escaped_rather_than_rendered(lever_run):
    """Supplier names and agent answers are data, not markup."""
    from core.table import load_table, write_table

    table = load_table(lever_run)
    table.loc[table.index[0], "supplier_normalized"] = "<script>alert(1)</script>"
    write_table(lever_run, table, "supplier_normalization")

    html = build_html(build_report(lever_run))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_a_run_with_nothing_to_show_still_produces_a_page(defective_run):
    html = build_html(build_report(defective_run))

    parser = _Wellformed()
    parser.feed(html)
    assert parser.stack == []
    assert "Procurement Lever Analysis" in html


def test_the_vega_runtime_matches_the_version_altair_writes():
    import altair as alt

    from export.html import _vl_version

    chosen = _vl_version()
    assert alt.SCHEMA_URL.split("/vega-lite/v")[1].startswith(chosen.split(".")[0])


def test_a_supplier_name_cannot_close_the_script_element(lever_run):
    """The specification carries values from the data into a <script> block.

    A browser looks for "</script>" before it looks for JSON, so a name shaped
    like one would end the block and run whatever followed.
    """
    from core.table import load_table, write_table

    table = load_table(lever_run)
    hostile = "</script><script>alert(1)</script>"
    table.loc[table.index[0], "supplier_normalized"] = hostile
    write_table(lever_run, table, "supplier_normalization")

    html = build_html(build_report(lever_run))

    assert hostile not in html
    assert "\\u003c/script\\u003e" in html
    # And the escaped form is still valid JSON the browser will parse.
    specs = json.loads("{" + re.search(r"const SPECS = \{(.*)\n\};", html, re.S).group(1) + "}")
    assert any(hostile in json.dumps(spec) for spec in specs.values())
