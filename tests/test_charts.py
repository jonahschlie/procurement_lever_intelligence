import pandas as pd
import pytest

from analysis import charts


def test_waterfall_steps_start_where_the_previous_one_ended():
    chain = [
        {"label": "Gross", "amount": 1000.0, "delta": None},
        {"label": "Credits", "amount": 200.0, "delta": -200.0},
        {"label": "Net", "amount": 800.0, "delta": None},
        {"label": "Intercompany", "amount": 100.0, "delta": -100.0},
        {"label": "Third party", "amount": 700.0, "delta": None},
    ]

    figure = charts.spend_waterfall(chain)
    rows = figure.data.set_index("step")

    assert rows.loc["Gross", "end"] == 1000.0
    # The deduction hangs off the total before it.
    assert rows.loc["Credits", "start"] == 1000.0
    assert rows.loc["Credits", "end"] == 800.0
    assert rows.loc["Intercompany", "start"] == 800.0
    assert rows.loc["Intercompany", "end"] == 700.0


def test_the_last_waterfall_step_is_marked_as_the_result():
    chain = [
        {"label": "Gross", "amount": 100.0, "delta": None},
        {"label": "Addressable", "amount": 80.0, "delta": None},
    ]

    roles = list(charts.spend_waterfall(chain).data["role"])

    assert roles[-1] == "result"


def test_the_pie_folds_the_tail_and_still_sums_to_one():
    spend = pd.Series({f"S{i}": float(100 - i) for i in range(20)})

    figure = charts.supplier_share(spend)

    # Five named slices plus one "Other" -- the readable maximum.
    assert len(figure.data) == 6
    assert figure.data["supplier"].iloc[-1].startswith("Other")
    assert figure.data["share"].sum() == pytest.approx(1.0)


def test_a_short_list_needs_no_other_slice():
    spend = pd.Series({"A": 60.0, "B": 40.0})

    figure = charts.supplier_share(spend)

    assert list(figure.data["supplier"]) == ["A", "B"]


def test_the_ranking_carries_cumulative_share_the_pie_cannot_show():
    spend = pd.Series({"A": 50.0, "B": 30.0, "C": 20.0})

    figure = charts.supplier_ranking(spend)

    assert list(figure.data["cumulative"].round(2)) == [0.5, 0.8, 1.0]


@pytest.mark.parametrize(
    "function,empty",
    [
        (charts.spend_waterfall, []),
        (charts.supplier_share, pd.Series(dtype=float)),
        (charts.supplier_ranking, pd.Series(dtype=float)),
        (charts.monthly_spend, pd.DataFrame()),
        (charts.contract_coverage, pd.DataFrame()),
        (charts.lever_allocation, pd.DataFrame()),
    ],
)
def test_empty_input_yields_an_empty_chart_not_an_error(function, empty):
    figure = function(empty)

    assert figure.data.empty
    assert figure.chart is not None


def test_every_figure_carries_the_numbers_behind_it():
    # The contrast warning is only dismissable against a table view.
    figure = charts.supplier_share(pd.Series({"A": 1.0}))

    assert isinstance(figure.data, pd.DataFrame)


def test_the_view_population_is_empty_where_a_stage_has_not_run():
    """Eligibility, the euro amount and the canonical supplier come from three
    different stages. Missing one is a shorter report, not a crash."""
    import pandas as pd

    from analysis import views

    flagged_only = pd.DataFrame({"include_addressable_spend": [True, True]})
    assert views.addressable(flagged_only).empty

    no_supplier_yet = pd.DataFrame(
        {"include_addressable_spend": [True], "amount_eur": [100.0]}
    )
    assert views.addressable(no_supplier_yet).empty
