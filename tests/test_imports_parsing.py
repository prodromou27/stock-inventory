import pytest
from django.core.exceptions import ValidationError

from apps.imports import parsing

from .imports_fixture_builder import build_legacy_workbook_bytes

CSV_HEADER = ",".join(parsing.COLUMNS)


def test_parses_synthetic_legacy_workbook():
    """Mirrors the real legacy workbook's layout/quirks (trailing-space
    headers, LOCATION="Customer" rows, datetime-typed date cells) without
    depending on any real customer data.
    """
    rows = parsing.parse_rows(
        filename="legacy_sample.xlsx", file_bytes=build_legacy_workbook_bytes()
    )
    assert len(rows) == 7
    row_number, first = rows[0]
    assert row_number == 2
    assert first["BRAND"] == "Acme Telecom "
    assert first["MODEL/Part No./SKU"] == "AT-D70"
    assert first["S/N"] == "1SYNTH01LF"
    assert first["LOCATION"] == "Customer"


def test_parses_csv_basic():
    csv_bytes = (CSV_HEADER + "\nFortinet,FG-100F,Firewall,SN1,1,Room A,,,,,,,,,,\n").encode(
        "utf-8"
    )
    rows = parsing.parse_rows(filename="test.csv", file_bytes=csv_bytes)
    assert len(rows) == 1
    row_number, data = rows[0]
    assert row_number == 2
    assert data["BRAND"] == "Fortinet"
    assert data["S/N"] == "SN1"


def test_blank_rows_are_skipped():
    csv_bytes = (
        CSV_HEADER + "\nFortinet,FG-100F,Firewall,SN1,1,Room A,,,,,,,,,,\n" + ",,,,,,,,,,,,,,,\n"
        "Cisco,C881,Router,SN2,1,Room A,,,,,,,,,,\n"
    ).encode("utf-8")
    rows = parsing.parse_rows(filename="test.csv", file_bytes=csv_bytes)
    assert len(rows) == 2
    assert [r[0] for r in rows] == [2, 4]


def test_missing_required_column_raises():
    bad_header = "BRAND,TYPE/DESCRIPTION,S/N,QTY,LOCATION\n"
    with pytest.raises(ValidationError, match="MODEL/Part No./SKU"):
        parsing.parse_rows(filename="test.csv", file_bytes=bad_header.encode("utf-8"))


def test_unsupported_extension_raises():
    with pytest.raises(ValidationError):
        parsing.parse_rows(filename="test.txt", file_bytes=b"whatever")


def test_no_header_row_raises():
    with pytest.raises(ValidationError, match="header row"):
        parsing.parse_rows(filename="test.csv", file_bytes=b"")


def test_checksum_is_stable_for_identical_bytes():
    data = b"hello world"
    assert parsing.compute_checksum(data) == parsing.compute_checksum(data)
    assert parsing.compute_checksum(data) != parsing.compute_checksum(b"hello worlds")
