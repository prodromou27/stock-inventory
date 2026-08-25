"""Whitespace/date/serial normalization for staged import rows (doc 07 step 3).
Never discards raw_data — callers keep both raw and normalized values.
"""

import datetime

from apps.core.text import normalize_whitespace
from apps.inventory.services.duplicates import normalize_serial

# Tried in order; day-first (European) formats take priority over month-first
# per doc 10's "corporate date format" default (non-blocking, conservative).
_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y"]


def normalize_text(value):
    return normalize_whitespace(str(value)) if value is not None else ""


def normalize_quantity(value):
    """Returns (int_or_none, was_valid). A blank value is valid-and-None
    (no quantity given); a non-numeric or non-positive value is invalid.
    """
    if value is None or str(value).strip() == "":
        return None, True
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None, False
    if as_float != int(as_float) or int(as_float) < 0:
        return None, False
    return int(as_float), True


def parse_legacy_date(value):
    """Returns (date_or_none, was_valid). Accepts a datetime/date object
    (openpyxl's native representation for formatted date cells) or a string
    in one of _DATE_FORMATS. A blank value is valid-and-None.
    """
    if value is None or str(value).strip() == "":
        return None, True
    if isinstance(value, datetime.datetime):
        return value.date(), True
    if isinstance(value, datetime.date):
        return value, True

    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt).date(), True
        except ValueError:
            continue
    return None, False


def normalize_vendor_serial(value):
    text = normalize_text(value)
    return text, normalize_serial(text)
