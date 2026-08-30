import pandas as pd
import pytest

from analysis.spend_report import spend_chain


def _table(rows):
    return pd.DataFrame(rows)


def _amounts(chain):
    return {step.label: step.amount for step in chain}


def test_the_chain_narrows_from_gross_to_addressable():
    table = _table(
        [
            # gross 100 + 200 + 50 + 40 = 390, one credit of -40
            {"amount_eur": 100.0, "include_spend_analysis": True, "flag_intercompany": False, "flag_non_addressable": False, "supplier_normalized": "Atlas"},
            {"amount_eur": 200.0, "include_spend_analysis": True, "flag_intercompany": False, "flag_non_addressable": False, "supplier_normalized": "Sopra"},
            {"amount_eur": -40.0, "include_spend_analysis": True, "flag_intercompany": False, "flag_non_addressable": False, "supplier_normalized": "Atlas"},
            {"amount_eur": 50.0, "include_spend_analysis": True, "flag_intercompany": True, "flag_non_addressable": False, "supplier_normalized": "Group SA"},
            {"amount_eur": 40.0, "include_spend_analysis": True, "flag_intercompany": False, "flag_non_addressable": True, "supplier_normalized": "Tax office"},
            # excluded from the analysis entirely
            {"amount_eur": 9999.0, "include_spend_analysis": False, "flag_intercompany": False, "flag_non_addressable": False, "supplier_normalized": ""},
        ]
    )

    report = spend_chain(table)
    amounts = _amounts(report.chain)

    assert amounts["Gross spend"] == 390.0
    assert amounts["Credit notes"] == 40.0
    assert amounts["Net spend"] == 350.0
    assert amounts["Intercompany"] == 50.0
    assert amounts["Third party spend"] == 300.0
    assert amounts["Not addressable"] == 40.0
    assert amounts["Addressable spend"] == 260.0


def test_every_step_reconciles_against_the_one_before():
    table = _table(
        [
            {"amount_eur": v, "include_spend_analysis": True, "flag_intercompany": ic,
             "flag_non_addressable": na, "supplier_normalized": "x"}
            for v, ic, na in [(500.0, False, False), (-100.0, False, False), (80.0, True, False), (60.0, False, True)]
        ]
    )

    amounts = _amounts(spend_chain(table).chain)

    assert amounts["Net spend"] == pytest.approx(amounts["Gross spend"] - amounts["Credit notes"])
    assert amounts["Third party spend"] == pytest.approx(
        amounts["Net spend"] - amounts["Intercompany"]
    )
    assert amounts["Addressable spend"] == pytest.approx(
        amounts["Third party spend"] - amounts["Not addressable"]
    )


def test_rows_outside_the_analysis_never_enter_any_step():
    table = _table(
        [
            {"amount_eur": 100.0, "include_spend_analysis": True, "flag_intercompany": False, "flag_non_addressable": False, "supplier_normalized": "a"},
            {"amount_eur": 5000.0, "include_spend_analysis": False, "flag_intercompany": False, "flag_non_addressable": False, "supplier_normalized": "b"},
        ]
    )

    report = spend_chain(table)

    assert _amounts(report.chain)["Gross spend"] == 100.0
    assert (report.rows_total, report.rows_analysed) == (2, 1)


def test_a_row_counted_as_intercompany_is_not_counted_again_as_unaddressable():
    # Both flags set: the row must leave the chain exactly once.
    table = _table(
        [
            {"amount_eur": 100.0, "include_spend_analysis": True, "flag_intercompany": False, "flag_non_addressable": False, "supplier_normalized": "a"},
            {"amount_eur": 60.0, "include_spend_analysis": True, "flag_intercompany": True, "flag_non_addressable": True, "supplier_normalized": "Group"},
        ]
    )

    amounts = _amounts(spend_chain(table).chain)

    assert amounts["Intercompany"] == 60.0
    assert amounts["Not addressable"] == 0.0
    assert amounts["Addressable spend"] == 100.0


def test_missing_flag_columns_mean_nothing_is_excluded():
    table = _table([{"amount_eur": 100.0, "include_spend_analysis": True}])

    amounts = _amounts(spend_chain(table).chain)

    assert amounts["Addressable spend"] == 100.0


def test_intercompany_entities_are_named():
    table = _table(
        [
            {"amount_eur": 50.0, "include_spend_analysis": True, "flag_intercompany": True, "flag_non_addressable": False, "supplier_normalized": "Zenith Iberia"},
            {"amount_eur": 10.0, "include_spend_analysis": True, "flag_intercompany": False, "flag_non_addressable": False, "supplier_normalized": "Atlas"},
        ]
    )

    report = spend_chain(table)

    assert report.intercompany_suppliers == ["Zenith Iberia"]
    assert report.intercompany_rows == 1


def test_the_chain_runs_on_to_what_the_levers_can_act_on(run_root):
    """Addressable spend is not the lever base: every lever needs a counterparty.

    Left implicit, the report and the lever page quote different figures under the
    same word, and the difference appears on neither screen.
    """
    import pandas as pd

    from analysis.spend_report import spend_chain

    table = pd.DataFrame(
        {
            "amount_eur": [100.0, 60.0, 40.0],
            "include_spend_analysis": [True, True, True],
            "flag_intercompany": [False, False, False],
            "flag_non_addressable": [False, False, False],
            "supplier_normalized": ["Atlas", "", "Sopra"],
        }
    )

    steps = {step.label: step.amount for step in spend_chain(table).chain}

    assert steps["Addressable spend"] == 200.0
    assert steps["No supplier name"] == 60.0
    assert steps["Analysable spend"] == 140.0


def test_a_row_already_deducted_is_not_deducted_twice(run_root):
    """An unnamed supplier inside intercompany or non-addressable spend has already
    left the chain, so it must not be subtracted again."""
    import pandas as pd

    from analysis.spend_report import spend_chain

    table = pd.DataFrame(
        {
            "amount_eur": [100.0, 50.0, 30.0],
            "include_spend_analysis": [True, True, True],
            "flag_intercompany": [False, True, False],
            "flag_non_addressable": [False, False, True],
            "supplier_normalized": ["Atlas", "", ""],
        }
    )

    steps = {step.label: step.amount for step in spend_chain(table).chain}

    assert steps["Addressable spend"] == 100.0
    assert steps["No supplier name"] == 0.0
    assert steps["Analysable spend"] == 100.0
