"""Display formatting: readable on screen, untouched underneath."""

import pandas as pd

from ui.format import BLANK, as_money, eur, eur_compact


def test_amounts_get_thousand_separators_and_no_decimals():
    assert eur(121107614.16) == "121,107,614"
    assert eur(-3188054.5) == "-3,188,054"
    assert eur(0) == "0"


def test_compact_switches_unit_at_each_magnitude():
    assert eur_compact(121107614.16) == "121.1m"
    assert eur_compact(3188.4) == "3.2k"
    assert eur_compact(1_500_000_000) == "1.5bn"
    assert eur_compact(842) == "842"


def test_a_missing_figure_shows_a_dash_rather_than_failing():
    assert eur(None) == BLANK
    assert eur(float("nan")) == BLANK
    assert eur_compact(None) == BLANK


def test_as_money_rounds_for_display_and_leaves_the_source_frame_alone():
    frame = pd.DataFrame({"Spend (EUR)": [121107614.16, None], "Rows": [3, 4]})
    shown = as_money(frame, "Spend (EUR)")

    assert shown["Spend (EUR)"].tolist() == [121107614, pd.NA]
    assert str(shown["Spend (EUR)"].dtype) == "Int64"
    # The caller's frame keeps every cent -- formatting never edits the data.
    assert frame["Spend (EUR)"][0] == 121107614.16


def test_as_money_ignores_a_column_the_frame_does_not_have():
    frame = pd.DataFrame({"Rows": [1]})
    assert as_money(frame, "Spend (EUR)").equals(frame)
