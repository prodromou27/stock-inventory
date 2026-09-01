from django.core.exceptions import PermissionDenied, ValidationError

from apps.core.authorization import is_administrator

from .models import SavedReport
from .report_builder import ALLOWED_FILTER_OPS, REPORTABLE_FIELDS


def create_saved_report(*, user, name, base_model, selected_fields, filters, is_shared=False):
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

    clean_filters = [
        {"field_key": row["field_key"], "op": row["op"], "value": row["value"]}
        for row in filters
        if row.get("field_key") in fields
        and row.get("op") in ALLOWED_FILTER_OPS
        and row.get("value")
    ]

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
