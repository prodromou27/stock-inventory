"""Turns a SavedReport (or an in-progress, not-yet-saved selection) into an
actual queryset. The one rule that must never be violated: every base
queryset is passed through this model's scoping function *before* any
selected_fields/filters — both user-controlled — are ever applied, exactly
the same location-honoring guarantee every other report in apps.reporting
already gives (queries.py's module docstring), just for a user-composed
report instead of a fixed one.

selected_fields/filters are never turned into raw ORM lookups from
arbitrary strings — every field/filter key is resolved through
REPORTABLE_FIELDS, a per-base_model allow-list dict (the same "user
supplies a key, never a raw ORM path" pattern apps.core.sorting.
SortableListMixin already established for sortable columns). An
unrecognized key or a disallowed operator is silently dropped, never
interpolated into `.filter(**{...})`.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.inventory.access import (
    scope_asset_status_history_queryset,
    scope_transaction_line_queryset,
    scope_transaction_queryset,
)
from apps.inventory.models import (
    AssetStatusHistory,
    InventoryTransaction,
    InventoryTransactionLine,
    StockBalance,
    UnitAsset,
)
from apps.locations.scoping import scope_queryset

from .models import ReportBaseModel

ALLOWED_FILTER_OPS = ("exact", "icontains", "gte", "lte", "in")

REPORTABLE_FIELDS = {
    ReportBaseModel.UNIT_ASSET: {
        "brand": "product__brand__name",
        "model": "product__model",
        "sku": "product__sku",
        "type": "product__product_type__name",
        "serial": "vendor_serial",
        "status": "status",
        "location": "current_location__name",
        "project_reference": "project_reference",
        "final_customer": "final_customer",
        "supplier": "supplier",
        "condition": "condition",
        "arrival_date": "arrival_date",
    },
    ReportBaseModel.STOCK_BALANCE: {
        "brand": "product__brand__name",
        "model": "product__model",
        "sku": "product__sku",
        "type": "product__product_type__name",
        "location": "location__name",
        "on_hand_quantity": "on_hand_quantity",
        "reserved_quantity": "reserved_quantity",
    },
    ReportBaseModel.TRANSACTION: {
        "transaction_number": "transaction_number",
        "movement_type": "movement_type",
        "occurred_at": "occurred_at",
        "performed_by": "performed_by__username",
        "employee_name": "employee_name",
        "final_customer": "final_customer",
        "project_reference": "project_reference",
        "source_location": "source_location__name",
        "destination_location": "destination_location__name",
        "is_temporary_assignment": "is_temporary_assignment",
    },
    ReportBaseModel.TRANSACTION_LINE: {
        "transaction_number": "transaction__transaction_number",
        "occurred_at": "transaction__occurred_at",
        "line_number": "line_number",
        "brand": "brand_snapshot",
        "model": "model_snapshot",
        "sku": "sku_snapshot",
        "type": "type_snapshot",
        "serial": "serial_snapshot",
        "quantity_delta": "quantity_delta",
        "from_location": "from_location__name",
        "to_location": "to_location__name",
        "condition": "condition_snapshot",
    },
    ReportBaseModel.STATUS_HISTORY: {
        "asset_serial": "unit_asset__vendor_serial",
        "brand": "unit_asset__product__brand__name",
        "model": "unit_asset__product__model",
        "from_status": "from_status",
        "to_status": "to_status",
        "from_location": "from_location__name",
        "to_location": "to_location__name",
        "occurred_at": "occurred_at",
        "recorded_by": "recorded_by__username",
    },
}

_BASE_MODEL_CLASSES = {
    ReportBaseModel.UNIT_ASSET: UnitAsset,
    ReportBaseModel.STOCK_BALANCE: StockBalance,
    ReportBaseModel.TRANSACTION: InventoryTransaction,
    ReportBaseModel.TRANSACTION_LINE: InventoryTransactionLine,
    ReportBaseModel.STATUS_HISTORY: AssetStatusHistory,
}


def _report_field(base_model, field_key):
    """Resolve an allow-listed report key to its concrete terminal model field."""
    orm_path = REPORTABLE_FIELDS.get(base_model, {}).get(field_key)
    model = _BASE_MODEL_CLASSES.get(base_model)
    if orm_path is None or model is None:
        raise ValidationError("Unknown report filter field.")
    field = None
    for component in orm_path.split("__"):
        field = model._meta.get_field(component)
        if field.is_relation:
            model = field.related_model
    return field


def normalize_filter_value(*, base_model, field_key, op, value):
    """Validate and coerce a filter before it reaches the database driver."""
    field = _report_field(base_model, field_key)
    if op == "icontains":
        if not isinstance(field, (models.CharField, models.TextField)):
            raise ValidationError("Contains filters can only be used with text fields.")
        return str(value)

    raw_values = (
        [part.strip() for part in str(value).split(",") if part.strip()] if op == "in" else [value]
    )
    if not raw_values:
        raise ValidationError("Enter at least one filter value.")
    try:
        normalized = [field.to_python(raw_value) for raw_value in raw_values]
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValidationError(
            f"'{value}' is not a valid value for {field_key.replace('_', ' ')}."
        ) from exc
    return normalized if op == "in" else normalized[0]


def _scoped_base_queryset(base_model, user):
    if base_model == ReportBaseModel.UNIT_ASSET:
        return scope_queryset(user, UnitAsset.objects.all(), location_field="current_location")
    if base_model == ReportBaseModel.STOCK_BALANCE:
        return scope_queryset(user, StockBalance.objects.all(), location_field="location")
    if base_model == ReportBaseModel.TRANSACTION:
        return scope_transaction_queryset(user, InventoryTransaction.objects.all())
    if base_model == ReportBaseModel.TRANSACTION_LINE:
        return scope_transaction_line_queryset(user, InventoryTransactionLine.objects.all())
    if base_model == ReportBaseModel.STATUS_HISTORY:
        return scope_asset_status_history_queryset(user, AssetStatusHistory.objects.all())
    raise ValueError(f"Unknown base_model: {base_model!r}")


def field_choices(base_model):
    """(key, label) choices for a field-selection widget — every key is
    guaranteed to exist in REPORTABLE_FIELDS for this base_model.
    """
    return [(key, key.replace("_", " ").title()) for key in REPORTABLE_FIELDS.get(base_model, {})]


def build_queryset(*, user, base_model, selected_fields, filters):
    """Returns (columns, queryset) — `columns` is the ordered list of
    friendly field keys actually used (never empty; falls back to every
    field for this base_model if selected_fields ends up empty after
    filtering out unrecognized keys). `queryset` is a plain, lazy, sliceable
    `.values(*orm_paths)` queryset whose row dicts are keyed by the
    underlying ORM *paths*, not the friendly names — a friendly key can
    collide with a real field name on the model (e.g. "status" on
    UnitAsset), which Django's `.values(key=F(...))` refuses as an
    annotation alias ("conflicts with a field on the model"); plain
    positional `.values()` has no such restriction. Use friendly_rows() to
    convert a (possibly sliced/capped) result into friendly-keyed rows.

    Scoping is applied to the base queryset before anything else. Every
    filter/field key not present in REPORTABLE_FIELDS[base_model], and
    every filter operator not in ALLOWED_FILTER_OPS, is silently dropped —
    this degrades gracefully (fewer columns/filters than requested) rather
    than raising, so a SavedReport referencing a field that's since been
    removed from the allow-list still runs.
    """
    fields = REPORTABLE_FIELDS.get(base_model, {})
    queryset = _scoped_base_queryset(base_model, user)

    query = Q()
    has_filter = False
    for row in filters or []:
        field_key = row.get("field_key")
        op = row.get("op")
        value = row.get("value")
        orm_path = fields.get(field_key)
        if orm_path is None or op not in ALLOWED_FILTER_OPS or value in (None, ""):
            continue
        try:
            value = normalize_filter_value(
                base_model=base_model, field_key=field_key, op=op, value=value
            )
        except ValidationError:
            # A legacy/corrupted SavedReport must degrade safely instead of
            # raising a database conversion error and returning HTTP 500.
            continue
        query &= Q(**{f"{orm_path}__{op}": value})
        has_filter = True
    if has_filter:
        queryset = queryset.filter(query)

    columns = [key for key in (selected_fields or []) if key in fields]
    if not columns:
        columns = list(fields.keys())
    orm_paths = [fields[key] for key in columns]
    return columns, queryset.values(*orm_paths).distinct()


def friendly_rows(columns, base_model, rows):
    """Remaps an iterable of build_queryset() row dicts (keyed by ORM
    path) into dicts keyed by the friendly `columns` names.
    """
    fields = REPORTABLE_FIELDS[base_model]
    return [{key: row[fields[key]] for key in columns} for row in rows]
