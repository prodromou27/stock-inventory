"""Reads the legacy Excel/CSV layout (spec §13, docs/architecture/07-excel-import.md)
into a list of raw-cell-value dicts, one per non-blank spreadsheet row. Column
matching is exact (trimmed, case-insensitive) against the known legacy header
set — never fuzzy-guessed, per spec §13's "do not guess" instruction.
"""

import csv
import hashlib
import io

import openpyxl
from django.core.exceptions import ValidationError

# Canonical column order also used by the downloadable template (services.py).
COLUMNS = [
    "BRAND",
    "MODEL/Part No./SKU",
    "TYPE/DESCRIPTION",
    "S/N",
    "QTY",
    "LOCATION",
    "2nd floor Location",
    "Project Ref. #",
    "FINAL CUSTOMER",
    "COMMENTS/#No",
    "PRODUCT DELIVERY / PRODUCT REMOVAL",
    "Arrival Date",
    "Delivery Date",
    "Return Date",
    "Removal Date",
    "Registrar",
]

REQUIRED_COLUMNS = ["BRAND", "MODEL/Part No./SKU", "TYPE/DESCRIPTION", "S/N", "QTY", "LOCATION"]


def _normalize_header(value):
    return " ".join(str(value or "").split()).casefold()


_HEADER_LOOKUP = {_normalize_header(col): col for col in COLUMNS}


def compute_checksum(file_bytes):
    return hashlib.sha256(file_bytes).hexdigest()


def _map_headers(header_row):
    """Returns {canonical_column_name: source_index}. Raises ValidationError
    naming any missing REQUIRED_COLUMNS. Unknown extra columns are ignored
    (a user's file may carry stray columns we don't map).
    """
    mapping = {}
    for index, cell in enumerate(header_row):
        canonical = _HEADER_LOOKUP.get(_normalize_header(cell))
        if canonical and canonical not in mapping:
            mapping[canonical] = index

    missing = [col for col in REQUIRED_COLUMNS if col not in mapping]
    if missing:
        raise ValidationError(
            "The file is missing required column(s): " + ", ".join(missing) + ". "
            "Download the template for the expected layout."
        )
    return mapping


def _row_is_blank(row):
    return all(cell is None or str(cell).strip() == "" for cell in row)


def parse_rows(*, filename, file_bytes):
    """Returns a list of (row_number, {column_name: raw_value}) tuples,
    row_number being the 1-based spreadsheet row (header = row 1, so data
    starts at row 2), skipping entirely blank rows.
    """
    lower_name = filename.lower()
    if lower_name.endswith(".csv"):
        return _parse_csv(file_bytes)
    if lower_name.endswith(".xlsx"):
        return _parse_xlsx(file_bytes)
    raise ValidationError("Only .xlsx or .csv files are supported.")


def _parse_xlsx(file_bytes):
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    except Exception as exc:  # openpyxl raises several distinct exception types
        raise ValidationError(f"Could not read the Excel file: {exc}") from exc

    worksheet = workbook.worksheets[0]
    rows = worksheet.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration:
        raise ValidationError("The file has no header row.") from None

    mapping = _map_headers(header_row)
    result = []
    for row_number, row in enumerate(rows, start=2):
        if _row_is_blank(row):
            continue
        result.append((row_number, _extract(row, mapping)))
    return result


def _parse_csv(file_bytes):
    text = file_bytes.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    try:
        header_row = next(reader)
    except StopIteration:
        raise ValidationError("The file has no header row.") from None

    mapping = _map_headers(header_row)
    result = []
    for row_number, row in enumerate(reader, start=2):
        if _row_is_blank(row):
            continue
        result.append((row_number, _extract(row, mapping)))
    return result


def _extract(row, mapping):
    data = {}
    for column in COLUMNS:
        index = mapping.get(column)
        data[column] = row[index] if index is not None and index < len(row) else None
    return data
