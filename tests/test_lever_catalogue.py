import pandas as pd
import pytest

from core.canonical import CANONICAL_FIELDS, FIELDS_BY_TIER
from levers.definitions import BY_ID, LEVERS, SPEND_KINDS
from levers.engine import _data_requests, _measure, assess


def _rows(**over):
    base = {
        "amount_eur": [1000.0, 1000.0],
        "supplier_normalized": ["Atlas", "Atlas"],
        "company_name": ["Alpha", "Beta"],
        "supplier_contract_status": ["no", "no"],
        "purchase_order": ["", ""],
        "currency": ["EUR", "EUR"],
        "company": ["A", "A"],
        "supplier": ["Atlas", "Atlas"],
        "amount_local": [1000.0, 1000.0],
        "posting_date": ["2024-01-01", "2024-01-02"],
        "invoice_number": ["I1", "I2"],
        "flag_duplicate_transaction": [False, False],
    }
    base.update(over)
    return pd.DataFrame(base)


# --- the three states ------------------------------------------------------


def test_a_measurable_lever_that_finds_something_is_quantified():
    rows = _rows()
    lever = BY_ID["contract_coverage"]

    status, reason, missing = assess(lever, rows)
    result = _measure(lever, rows, lever.membership(rows), pd.Series(["contract_coverage"] * 2), status, reason, missing)

    assert result.status == "quantified"
    assert result.gross_base == 2000.0


def test_a_measurable_lever_that_finds_nothing_says_so():
    # The data can answer the question; the answer is simply "none".
    rows = _rows(supplier_contract_status=["yes", "yes"])
    lever = BY_ID["contract_coverage"]

    status, reason, missing = assess(lever, rows)
    result = _measure(lever, rows, lever.membership(rows), pd.Series(["", ""]), status, reason, missing)

    assert result.status == "not_applicable"
    assert "no spend qualifies" in result.status_reason
    assert result.missing_fields == []


def test_a_lever_whose_field_is_absent_names_what_is_missing():
    rows = _rows()  # no item_code, no quantity
    lever = BY_ID["price_harmonisation"]

    status, reason, missing = assess(lever, rows)

    assert status == "not_assessable"
    assert set(missing) == {"item_code", "quantity"}
    assert "Item Code" in reason and "Quantity" in reason


def test_the_two_zero_states_are_distinguishable():
    """The whole point: a zero from 'nothing found' must not read like a zero
    from 'could not look'."""
    found_nothing = assess(BY_ID["contract_coverage"], _rows(supplier_contract_status=["yes", "yes"]))
    could_not_look = assess(BY_ID["price_harmonisation"], _rows())

    assert found_nothing[0] != could_not_look[0]


# --- requirements ----------------------------------------------------------


def test_an_alternative_requirement_is_enough():
    # Price harmonisation accepts item_code+quantity+amount OR item_code+unit_price.
    with_price = _rows(item_code=["A1", "A1"], unit_price=[10.0, 12.0])

    status, _, missing = assess(BY_ID["price_harmonisation"], with_price)

    assert missing == []
    # No membership defined yet, so it stops there rather than claiming a figure.
    assert status == "not_assessable"


def test_a_present_but_empty_column_does_not_count_as_available():
    rows = _rows(item_code=["", ""], quantity=["", ""])

    _, _, missing = assess(BY_ID["price_harmonisation"], rows)

    assert set(missing) == {"item_code", "quantity"}


def test_the_closest_alternative_decides_what_is_reported_missing():
    # item_code is there, so the gap is the smaller one: just unit_price.
    rows = _rows(item_code=["A1", "A1"], unit_price=[1.0, 1.0])
    rows = rows.drop(columns=["unit_price"])

    _, _, missing = assess(BY_ID["price_harmonisation"], rows)

    assert missing == ["quantity"]


def test_a_field_that_exists_but_cannot_carry_the_lever_says_why():
    rows = _rows(category=["Consulting", "Consulting"])

    status, reason, missing = assess(BY_ID["category_consolidation"], rows)

    assert status == "not_assessable"
    assert missing == []  # the field is present; the content is the problem
    assert "duplicates the GL classification" in reason


# --- risk levers and totals ------------------------------------------------


def test_risk_levers_report_an_exposure_and_claim_no_spend():
    rows = _rows(currency=["PLN", "PLN"])
    lever = BY_ID["fx_exposure"]

    result = _measure(lever, rows, lever.membership(rows), pd.Series(["", ""]), "quantified", "", [])

    assert result.kind == "risk"
    assert result.gross_base == 2000.0
    assert result.net_base == 0.0  # never enters the savings total
    assert result.potential_base == 0.0
    assert "foreign currencies" in result.metric


def test_only_spend_levers_carry_rates():
    for lever in LEVERS:
        if lever.kind in SPEND_KINDS and lever.membership is not None:
            assert sum(lever.rates) > 0, lever.lever_id
        if lever.kind == "risk":
            assert sum(lever.rates) == 0, lever.lever_id


# --- catalogue integrity ---------------------------------------------------


def test_every_required_field_exists_in_the_canonical_schema():
    known = {field.key for field in CANONICAL_FIELDS}

    for lever in LEVERS:
        for option in lever.requires:
            assert option <= known, f"{lever.lever_id} requires unknown field(s)"


def test_field_dependent_levers_rest_on_extended_fields():
    extended = {field.key for field in FIELDS_BY_TIER["extended"]}

    price = BY_ID["price_harmonisation"]

    assert any(option & extended for option in price.requires)


def test_the_data_request_list_says_what_each_field_unlocks():
    from core.models import LeverResult

    results = [
        LeverResult(
            lever_id="price_harmonisation", name="Price Harmonisation", mechanism="",
            status="not_assessable", missing_fields=["item_code", "quantity"],
            gross_base=0, net_base=0, rows=0, suppliers=0, companies=0,
            rate_low=0, rate_base=0, rate_high=0,
            potential_low=0, potential_base=0, potential_high=0,
            effort="low", effort_reason="", confidence="low", confidence_reason="",
            contributors=[],
        ),
        LeverResult(
            lever_id="volume_rebates", name="Volume Rebates", mechanism="",
            status="not_assessable", missing_fields=["quantity"],
            gross_base=0, net_base=0, rows=0, suppliers=0, companies=0,
            rate_low=0, rate_base=0, rate_high=0,
            potential_low=0, potential_base=0, potential_high=0,
            effort="low", effort_reason="", confidence="low", confidence_reason="",
            contributors=[],
        ),
    ]

    requests = {r.field: r for r in _data_requests(results)}

    assert set(requests) == {"item_code", "quantity"}
    assert requests["quantity"].unlocks == ["Price Harmonisation", "Volume Rebates"]
    assert requests["item_code"].label == "Item Code"
