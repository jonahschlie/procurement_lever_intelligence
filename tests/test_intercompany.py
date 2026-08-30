import pandas as pd

from suppliers.intercompany import detect_intercompany, group_entities, group_stem


def _table(companies, suppliers, aggregate=None):
    rows = [
        {"company_name": company, "supplier": supplier, "flag_aggregate_row": False}
        for company, supplier in zip(companies, suppliers)
    ]
    if aggregate:
        rows.append({"company_name": aggregate[0], "supplier": aggregate[1], "flag_aggregate_row": True})
    return pd.DataFrame(rows)


def test_the_group_name_is_derived_not_configured():
    # No company name appears anywhere in the source; the stem comes from the data.
    entities = ["Zenith Power Iberia SL", "Zenith Energy Polska", "Zenith Comunidades SA"]

    assert group_stem(entities) == {"zenith"}


def test_a_supplier_matching_one_of_the_groups_companies_is_intercompany():
    table = _table(
        ["Zenith Power Iberia SL", "Zenith Energy Polska"],
        ["Atlas Freight", "Zenith Power Iberia SL"],
    )

    candidates = {c.supplier: c for c in detect_intercompany(table)}

    assert set(candidates) == {"Zenith Power Iberia SL"}
    assert "matches the group company" in candidates["Zenith Power Iberia SL"].reasons[0]


def test_the_shared_stem_catches_an_entity_spelled_differently():
    table = _table(
        ["Zenith Power Iberia SL", "Zenith Energy Polska", "Zenith Comunidades SA"],
        ["Atlas Freight", "ZENITH SERVICES", "Sopra Steria"],
    )

    candidates = {c.supplier for c in detect_intercompany(table)}

    # Not one of the listed companies, but unmistakably the same group.
    assert candidates == {"ZENITH SERVICES"}


def test_both_signals_are_reported_when_they_agree():
    table = _table(["Zenith Power SL", "Zenith Energy SA"], ["Zenith Power SL", "Atlas"])

    candidate = detect_intercompany(table)[0]

    assert len(candidate.reasons) == 2
    assert any("matches the group company" in r for r in candidate.reasons)
    assert any("carries the group name" in r for r in candidate.reasons)


def test_an_aggregate_row_cannot_nominate_itself_as_a_company():
    # The grand total row carries the group's name in its company column and its
    # own marker as a supplier. Left in, it would appear as an intercompany entity.
    table = _table(
        ["Zenith Power SL", "Zenith Energy SA"],
        ["Atlas Freight", "Sopra Steria"],
        aggregate=("GRAND TOTAL", "*** GRAND TOTAL ***"),
    )

    assert "GRAND TOTAL" not in group_entities(table)
    assert detect_intercompany(table) == []


def test_a_single_company_has_no_stem_to_share():
    # One entity cannot establish a group name, so only direct matches count.
    table = _table(["Zenith Power SL"], ["Zenith Logistics"])

    assert group_stem(["Zenith Power SL"]) == set()
    assert detect_intercompany(table) == []


def test_numeric_company_codes_are_not_treated_as_names():
    table = pd.DataFrame(
        [{"company": "1101", "company_name": "", "supplier": "1101", "flag_aggregate_row": False}]
    )

    assert group_entities(table) == []
    assert detect_intercompany(table) == []


def test_unrelated_suppliers_are_left_alone():
    table = _table(
        ["Zenith Power SL", "Zenith Energy SA"],
        ["Atlas Freight & Logistics", "Sopra Steria", "Baltic Fuel Supply"],
    )

    assert detect_intercompany(table) == []
