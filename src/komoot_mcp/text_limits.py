"""Shared text limits for prepared navigation descriptions."""

MAX_DESCRIPTION_UNITS = 250


def utf16_units(text):
    """Return the number of UTF-16 code units used by text."""
    return len(text.encode("utf-16-le")) // 2
