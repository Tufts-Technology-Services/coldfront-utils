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
