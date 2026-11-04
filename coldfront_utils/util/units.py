from decimal import Decimal


def bytes_to_units(value, units="TB") -> float:
    units = units.lower().strip()
    if units == "tb":
        return value / (1000**4)
    elif units == "gb":
        return value / (1000**3)
    elif units == "tib":
        return value / (1024**4)
    elif units == "gib":
        return value / (1024**3)
    else:
        raise ValueError(f"Unrecognized storage unit '{units}'")


def units_to_bytes(value, units="TB") -> int:
    """
    `value` `units` as a whole number of bytes, dropping any fraction of a byte.

    `value` may be an int, float, Decimal or numeric string. The arithmetic is done in Decimal,
    taking a float at its shortest decimal form (str(2.01) is '2.01'), not its binary value: 2.01
    is stored as 2.00999999999999978..., and multiplied out as a float came to 2,009,999,999,999
    bytes for 2.01 TB, one short.
    """
    amount = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    units = units.lower().strip()
    if units == "tb":
        return int(amount * (1000**4))
    elif units == "gb":
        return int(amount * (1000**3))
    elif units == "tib":
        return int(amount * (1024**4))
    elif units == "gib":
        return int(amount * (1024**3))
    else:
        raise ValueError(f"Unrecognized storage unit '{units}'")
