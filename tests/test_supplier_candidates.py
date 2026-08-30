from suppliers.candidates import build_candidates, normalize_name, similarity


def test_normal_form_ignores_case_punctuation_suffixes_and_connectives():
    assert normalize_name("HELVETIA STUDIES AND REPORTS") == "helvetia studies reports"
    assert normalize_name("Helvetia Studies & Reports") == "helvetia studies reports"
    assert normalize_name("Müller Logistik GmbH") == "müller logistik"
    assert normalize_name("Aurora Subcontracting, S.A") == "aurora subcontracting"


def test_cleanup_identical_names_score_one():
    assert similarity("BALTIC FUEL SUPPLY Sp. z o.o.", "Baltic Fuel Supply") == 1.0


def test_abbreviations_land_in_the_grey_zone():
    score = similarity("Atlas Frght & Log.", "Atlas Freight & Logistics")

    assert 0.70 <= score < 0.95


def test_unrelated_names_are_no_candidates():
    assert similarity("Atlas Freight", "Sopra Steria") < 0.70


def test_pairs_are_sorted_into_bands():
    auto, grey, _ = build_candidates(
        [
            "Helvetia Studies & Reports",
            "Helvetia Studies and Reports",
            "Atlas Frght & Log.",
            "Atlas Freight & Logistics",
            "Sopra Steria",
        ]
    )

    assert [(p.left, p.right) for p in auto] == [
        ("Helvetia Studies & Reports", "Helvetia Studies and Reports")
    ]
    assert [(p.left, p.right) for p in grey] == [
        ("Atlas Freight & Logistics", "Atlas Frght & Log.")
    ]
