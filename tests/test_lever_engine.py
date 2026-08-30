import pandas as pd
import pytest

from core.config import LEVER_PRECEDENCE
from levers.definitions import LEVERS, SPEND_KINDS
from levers.engine import _addressable, _assign_primary, _band, _effort, _measure


def _rows(records):
    return pd.DataFrame(records)


def _base_row(**overrides):
    row = {
        "amount_eur": 100.0,
        "supplier_normalized": "Atlas",
        "company_name": "Alpha",
        "supplier_contract_status": "yes",
        "purchase_order": "PO1",
        "include_addressable_spend": True,
        "company": "Alpha",
        "supplier": "Atlas",
        "amount_local": 100.0,
        "posting_date": "2024-01-01",
        "invoice_number": "INV",
        "flag_duplicate_transaction": False,
    }
    row.update(overrides)
    return row


def _memberships(rows):
    """Catalogue entries without a measurement never reach membership."""
    return {
        lever.lever_id: lever.membership(rows)
        for lever in LEVERS
        if lever.membership is not None
    }


def test_every_euro_is_assigned_to_exactly_one_lever():
    # The core guard against double counting: 64.5% of real spend qualifies for
    # more than one lever, so the net bases must still add up to the whole.
    rows = _rows(
        [
            _base_row(supplier_normalized="Atlas", company_name="Alpha", amount_eur=1000.0),
            _base_row(supplier_normalized="Atlas", company_name="Beta", amount_eur=1000.0),
            _base_row(
                supplier_normalized="Sopra", company_name="Alpha", amount_eur=500.0,
                supplier_contract_status="no",
            ),
            _base_row(
                supplier_normalized="Sopra", company_name="Beta", amount_eur=500.0,
                supplier_contract_status="no",
            ),
            _base_row(
                supplier_normalized="Tiny", company_name="Alpha", amount_eur=10.0,
                supplier_contract_status="unknown", purchase_order="",
            ),
        ]
    )
    memberships = _memberships(rows)
    primary = _assign_primary(rows, memberships)

    results = [
        _measure(lever, rows, memberships[lever.lever_id], primary, "quantified", "", [])
        for lever in LEVERS
        if lever.lever_id in memberships and lever.kind in SPEND_KINDS
    ]

    assert sum(r.net_base for r in results) == pytest.approx(rows["amount_eur"].sum())
    # Every row got exactly one lever, none left over.
    assert (primary != "").all()


def test_the_most_specific_lever_claims_a_shared_row():
    # This row qualifies for tail, maverick and contract at once.
    rows = _rows(
        [
            _base_row(
                supplier_normalized="Tiny", company_name="Alpha", amount_eur=1.0,
                supplier_contract_status="unknown", purchase_order="",
            ),
            _base_row(supplier_normalized="Big", company_name="Alpha", amount_eur=10_000.0),
            _base_row(supplier_normalized="Big", company_name="Beta", amount_eur=10_000.0),
        ]
    )

    primary = _assign_primary(rows, _memberships(rows))

    # Tail spend is the most specific of the three this row qualifies for.
    assert primary.iloc[0] == "tail_spend"
    assert LEVER_PRECEDENCE.index("tail_spend") < LEVER_PRECEDENCE.index("maverick")
    assert LEVER_PRECEDENCE.index("maverick") < LEVER_PRECEDENCE.index("contract_coverage")


def test_gross_and_net_differ_where_levers_overlap():
    rows = _rows(
        [
            _base_row(supplier_normalized="Atlas", company_name="Alpha", amount_eur=1000.0, supplier_contract_status="no"),
            _base_row(supplier_normalized="Atlas", company_name="Beta", amount_eur=1000.0, supplier_contract_status="no"),
        ]
    )
    memberships = _memberships(rows)
    primary = _assign_primary(rows, memberships)

    consolidation = _measure(
        next(l for l in LEVERS if l.lever_id == "supplier_consolidation"),
        rows, memberships["supplier_consolidation"], primary, "quantified", "", [],
    )

    # Both rows are consolidation candidates, but contract coverage claimed them.
    assert consolidation.gross_base == 2000.0
    assert consolidation.net_base == 0.0


def test_potential_is_base_times_rate_for_every_scenario():
    rows = _rows(
        [
            _base_row(supplier_normalized="Atlas", company_name="Alpha", amount_eur=1000.0),
            _base_row(supplier_normalized="Atlas", company_name="Beta", amount_eur=1000.0),
        ]
    )
    memberships = _memberships(rows)
    lever = next(l for l in LEVERS if l.lever_id == "supplier_consolidation")

    result = _measure(
        lever, rows, memberships["supplier_consolidation"],
        _assign_primary(rows, memberships), "quantified", "", [],
    )

    low, base, high = lever.rates
    assert result.potential_low == pytest.approx(result.net_base * low)
    assert result.potential_base == pytest.approx(result.net_base * base)
    assert result.potential_high == pytest.approx(result.net_base * high)


def test_a_lever_with_no_rows_still_appears_with_zero():
    # A lever that finds nothing must be visible as "nothing found", not vanish.
    rows = _rows([_base_row(supplier_normalized="Atlas", company_name="Alpha")])
    memberships = _memberships(rows)

    maverick = _measure(
        next(l for l in LEVERS if l.lever_id == "maverick"),
        rows, memberships["maverick"], _assign_primary(rows, memberships), "quantified", "", [],
    )

    assert (maverick.gross_base, maverick.net_base, maverick.potential_base) == (0.0, 0.0, 0.0)
    assert maverick.rows == 0


@pytest.mark.parametrize(
    "suppliers,companies,expected",
    [(5, 2, "low"), (5, 4, "medium"), (20, 2, "medium"), (50, 2, "high"), (5, 8, "high")],
)
def test_effort_takes_the_harder_of_the_two_counts(suppliers, companies, expected):
    level, reason = _effort(suppliers, companies)

    assert level == expected
    assert str(suppliers) in reason and str(companies) in reason


def test_confidence_states_why_it_is_not_high():
    maverick = next(l for l in LEVERS if l.lever_id == "maverick")
    contract = next(l for l in LEVERS if l.lever_id == "contract_coverage")

    # An absence-based signal cannot be high confidence.
    assert maverick.confidence == "low"
    assert "absence" in maverick.confidence_reason
    assert contract.confidence == "medium"
    assert "understated" in contract.confidence_reason


def test_only_addressable_rows_with_a_supplier_enter_any_lever():
    table = _rows(
        [
            _base_row(),
            _base_row(include_addressable_spend=False, amount_eur=9999.0),
            _base_row(supplier_normalized="   ", amount_eur=8888.0),
            _base_row(amount_eur=None),
        ]
    )

    rows = _addressable(table)

    assert len(rows) == 1
    assert rows["amount_eur"].sum() == 100.0


def test_duplicate_payments_counts_the_excess_not_every_flagged_row():
    """The flag marks both rows of a pair, but one booking is legitimate.

    Counting every flagged row would double the lever exactly -- on real data
    6,376,108 EUR instead of the recoverable 3,188,054.
    """
    from levers.definitions import BY_ID

    rows = _rows(
        [
            # One duplicated pair of 500 each, plus an unrelated booking.
            {**_base_row(amount_eur=500.0, invoice_number="INV1", posting_date="2024-01-01"),
             "company": "A", "supplier": "Atlas", "amount_local": 500.0,
             "flag_duplicate_transaction": True},
            {**_base_row(amount_eur=500.0, invoice_number="INV1", posting_date="2024-01-01"),
             "company": "A", "supplier": "Atlas", "amount_local": 500.0,
             "flag_duplicate_transaction": True},
            {**_base_row(amount_eur=300.0, invoice_number="INV2", posting_date="2024-02-01"),
             "company": "A", "supplier": "Atlas", "amount_local": 300.0,
             "flag_duplicate_transaction": False},
        ]
    )

    member = BY_ID["duplicate_payments"].membership(rows)

    assert rows.loc[member, "amount_eur"].sum() == 500.0  # the excess copy only
    assert int(member.sum()) == 1


def test_a_missing_duplicate_flag_is_not_an_error():
    from levers.definitions import BY_ID

    rows = _rows([_base_row()]).drop(columns=[], errors="ignore")

    member = BY_ID["duplicate_payments"].membership(rows)

    assert not member.any()
