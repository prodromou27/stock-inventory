"""Safety helpers for values exported to spreadsheet applications."""

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def spreadsheet_safe(value):
    """Prevent untrusted text from being interpreted as a spreadsheet formula."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def spreadsheet_safe_row(values):
    return [spreadsheet_safe(value) for value in values]
