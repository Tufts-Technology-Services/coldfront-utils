from decimal import Decimal

import pytest

from coldfront_utils import bytes_to_units, units_to_bytes


@pytest.mark.parametrize(
    ("units", "one_unit_in_bytes"),
    [("TB", 1000**4), ("GB", 1000**3), ("TiB", 1024**4), ("GiB", 1024**3)],
)
def test_conversions_round_trip(units, one_unit_in_bytes):
    assert units_to_bytes(1, units) == one_unit_in_bytes
    assert bytes_to_units(one_unit_in_bytes, units) == 1


def test_units_default_to_terabytes():
    assert units_to_bytes(2) == 2 * 1000**4
    assert bytes_to_units(2 * 1000**4) == 2


def test_unit_names_ignore_case_and_surrounding_space():
    assert units_to_bytes(1, " tib ") == 1024**4


def test_units_to_bytes_truncates_to_whole_bytes():
    assert units_to_bytes(1.5, "GB") == 1_500_000_000
    assert isinstance(units_to_bytes(0.0000000001, "GB"), int)


@pytest.mark.parametrize("convert", [bytes_to_units, units_to_bytes])
def test_unrecognized_units_are_rejected(convert):
    with pytest.raises(ValueError, match="Unrecognized storage unit 'pb'"):
        convert(1, "PB")


@pytest.mark.parametrize(("terabytes", "expected"), [(2.01, 2_010_000_000_000), (4.1, 4_100_000_000_000)])
def test_floats_convert_at_their_decimal_value(terabytes, expected):
    """2.01 as a float is 2.00999999999999978..., which multiplied out as a float was 1 byte short."""
    assert units_to_bytes(terabytes) == expected


def test_no_two_decimal_terabyte_value_loses_a_byte():
    short = [i for i in range(1, 10_001) if units_to_bytes(i / 100) != i * 10**10]
    assert short == []


@pytest.mark.parametrize("value", [Decimal("2.01"), "2.01", " 2.01 "])
def test_decimals_and_numeric_strings_are_accepted(value):
    assert units_to_bytes(value) == 2_010_000_000_000
