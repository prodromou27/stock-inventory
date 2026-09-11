import csv
import io

import openpyxl
from django.core.exceptions import PermissionDenied, ValidationError
from django.template.loader import render_to_string
from django.utils.text import slugify

from apps.core.authorization import is_administrator
from apps.core.spreadsheets import spreadsheet_safe_row

from .models import SavedReport
from .report_builder import (
    ALLOWED_FILTER_OPS,
    REPORTABLE_FIELDS,
    build_queryset,
    friendly_rows,
    normalize_filter_value,
)

SAVED_REPORT_PDF_ROW_CAP = 1000


def create_saved_report(
    *,
    user,
    name,
    base_model,
    selected_fields,
    filters,
    is_shared=False,
    sort_by="",
    sort_direction="asc",
):
    """Re-validates selected_fields/filters against REPORTABLE_FIELDS again
    at save time — never trusts that a caller (a form, a future API) already
    did this correctly; the same defense-in-depth apps.reporting.
    report_builder.build_queryset() applies at run time.
    """
    name = name.strip()
    if not name:
        raise ValidationError("Name is required.")
    if base_model not in REPORTABLE_FIELDS:
        raise ValidationError("Unknown report type.")

    fields = REPORTABLE_FIELDS[base_model]
    clean_fields = [key for key in selected_fields if key in fields]
    if not clean_fields:
        raise ValidationError("Choose at least one field.")
    if sort_by and sort_by not in clean_fields:
        raise ValidationError("Sort field must be one of the selected report columns.")
    if sort_direction not in {"asc", "desc"}:
        raise ValidationError("Unknown sort direction.")

    clean_filters = []
    for row in filters:
        field_key = row.get("field_key")
        op = row.get("op")
        value = row.get("value")
        if field_key not in fields or op not in ALLOWED_FILTER_OPS or not value:
            continue
        normalize_filter_value(base_model=base_model, field_key=field_key, op=op, value=value)
        clean_filters.append({"field_key": field_key, "op": op, "value": value})

    # Only an Administrator's reports can be shared with other users —
    # enforced here, not just hidden/disabled in the form, since the form
    # alone isn't a trusted boundary.
    if is_shared and not is_administrator(user):
        is_shared = False

    report = SavedReport(
        name=name,
        base_model=base_model,
        selected_fields=clean_fields,
        filters=clean_filters,
        sort_by=sort_by,
        sort_direction=sort_direction,
        is_shared=is_shared,
        created_by=user,
        updated_by=user,
    )
    report.full_clean()
    report.save()
    return report


def delete_saved_report(*, report, user):
    if report.created_by_id != user.id and not is_administrator(user):
        raise PermissionDenied("You can only delete your own saved reports.")
    report.delete()


def require_saved_report_access(*, report, user):
    if report.created_by_id != user.id and not report.is_shared:
        raise PermissionDenied("You cannot access this saved report.")


def build_saved_report_export(*, report, user, export_format):
    """Build a scoped export for a background worker.

    Stored report configuration is revalidated by ``build_queryset`` and all
    location scoping is applied for the requesting user at execution time.
    """
    require_saved_report_access(report=report, user=user)
    if export_format not in {"csv", "xlsx", "pdf"}:
        raise ValidationError("Choose CSV, Excel, or PDF.")
    columns, queryset = build_queryset(
        user=user,
        base_model=report.base_model,
        selected_fields=report.selected_fields,
        filters=report.filters,
        sort_by=report.sort_by,
        sort_direction=report.sort_direction,
    )
    filename = slugify(report.name) or "report"
    if export_format == "csv":
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(spreadsheet_safe_row(columns))
        for row in friendly_rows(columns, report.base_model, queryset.iterator(chunk_size=1000)):
            writer.writerow(spreadsheet_safe_row([row.get(column, "") for column in columns]))
        return buffer.getvalue().encode("utf-8-sig"), "text/csv", f"{filename}.csv"
    if export_format == "xlsx":
        workbook = openpyxl.Workbook(write_only=True)
        sheet = workbook.create_sheet("Report")
        sheet.append(spreadsheet_safe_row(columns))
        for row in friendly_rows(columns, report.base_model, queryset.iterator(chunk_size=1000)):
            sheet.append(spreadsheet_safe_row([row.get(column, "") for column in columns]))
        buffer = io.BytesIO()
        workbook.save(buffer)
        return (
            buffer.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            f"{filename}.xlsx",
        )

    raw_rows = list(queryset[:SAVED_REPORT_PDF_ROW_CAP])
    dict_rows = friendly_rows(columns, report.base_model, raw_rows)
    html = render_to_string(
        "reporting/saved_report_pdf.html",
        {
            "report": report,
            "columns": columns,
            "rows": [[row.get(column, "") for column in columns] for row in dict_rows],
            "truncated": queryset.count() > SAVED_REPORT_PDF_ROW_CAP,
            "row_cap": SAVED_REPORT_PDF_ROW_CAP,
        },
    )
    from weasyprint import HTML

    return (
        HTML(string=html).write_pdf(optimize_images=True, jpeg_quality=85, dpi=150),
        "application/pdf",
        f"{filename}.pdf",
    )
