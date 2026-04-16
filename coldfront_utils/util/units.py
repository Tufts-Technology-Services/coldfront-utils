


def bytes_to_units(value, units="TB") -> float:
    units = units.lower().strip()
    if units == "tb":
        return value / (1000 ** 4)
    elif units == "gb":
        return value / (1000 ** 3)
    elif units == "tib":
        return value / (1024 ** 4)
    elif units == "gib":
        return value / (1024 ** 3)
    else:
        raise ValueError(f"Unrecognized storage unit '{units}'")


def units_to_bytes(value, units="TB") -> int:
    units = units.lower().strip()
    if units == "tb":
        return int(value * (1000 ** 4))
    elif units == "gb":
        return int(value * (1000 ** 3))
    elif units == "tib":
        return int(value * (1024 ** 4))
    elif units == "gib":
        return int(value * (1024 ** 3))
    else:
        raise ValueError(f"Unrecognized storage unit '{units}'")