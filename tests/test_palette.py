from core import palette


def test_the_brand_purple_is_not_a_series_colour():
    """Measured, it sits at lightness 0.301 -- outside the permitted band -- and
    failed three of the validator's checks as a categorical hue. It stays the
    interface accent and the dark end of the sequential ramp."""
    assert palette.BRAND not in palette.CATEGORICAL


def test_the_categorical_set_is_the_validated_one_in_order():
    assert palette.CATEGORICAL == (
        "#8A4FB5", "#eb6834", "#1baf7a", "#eda100", "#2a78d6", "#e34948",
    )


def test_hues_are_handed_out_in_fixed_order_never_cycled():
    assert palette.categorical(3) == list(palette.CATEGORICAL[:3])
    assert palette.categorical(1)[0] == palette.CATEGORICAL[0]


def test_a_seventh_series_is_refused_rather_than_invented():
    # A generated hue is indistinguishable under colour vision deficiency.
    import pytest

    with pytest.raises(ValueError, match="fold the tail"):
        palette.categorical(len(palette.CATEGORICAL) + 1)


def test_the_sequential_ramp_ends_on_the_brand():
    assert palette.SEQUENTIAL[-1] == palette.BRAND
    assert len(palette.SEQUENTIAL) >= 3


def test_the_waterfall_avoids_a_good_bad_reading():
    # A deduction removes rows from a population; it is not a failure.
    assert palette.WATERFALL_DEDUCTION not in ("#e34948", "#1baf7a")


def test_a_pie_is_capped_at_a_readable_number_of_slices():
    assert palette.PIE_SLICES + 1 <= 6
