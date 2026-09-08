"""Audited corrections to one asset's descriptive details, never its ledger."""

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.shortcuts import get_object_or_404

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.catalog.services import resolve_or_create_product
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role

from ..access import require_asset_access, scope_asset_queryset
from ..models import UnitAsset
from .duplicates import check_duplicate_serial, duplicate_serial_count, normalize_serial
from .receipts import DuplicateSerialError

EDIT_FIELDS = (
    "name",
    "vendor_serial",
    "project_reference",
    "final_customer",
    "supplier",
    "invoice_number",
    "accessories",
    "notes",
)


def get_editable_asset(*, user, pk):
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    return get_object_or_404(
        scope_asset_queryset(
            user,
            UnitAsset.objects.select_related(
                "product__brand", "product__product_type", "current_location"
            ),
        ),
        pk=pk,
    )


@transaction.atomic
def edit_asset(
    *,
    user,
    pk,
    values,
    brand_name,
    model,
    sku,
    duplicate_serial_acknowledged=False,
    duplicate_product_acknowledged=False,
):
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    if set(values) - set(EDIT_FIELDS):
        raise ValidationError("Status, location, and tracking changes require a stock operation.")
    asset = get_object_or_404(UnitAsset.objects.select_for_update(), pk=pk)
    require_asset_access(user, asset)
    old = {field: getattr(asset, field) for field in EDIT_FIELDS}
    old.update(
        product_id=str(asset.product_id),
        brand=asset.product.brand.name,
        model=asset.product.model,
        sku=asset.product.sku,
    )
    serial = values.get("vendor_serial", asset.vendor_serial)
    matches = []
    duplicate_count = 0
    if normalize_serial(serial) != asset.normalized_serial and normalize_serial(serial):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [normalize_serial(serial)]
            )
        duplicate_count = duplicate_serial_count(serial, exclude_id=asset.pk)
        matches = list(check_duplicate_serial(serial, user=user, exclude_id=asset.pk))
        if duplicate_count and not duplicate_serial_acknowledged:
            raise DuplicateSerialError(matches)
    product = asset.product
    if (brand_name, model, sku) != (product.brand.name, product.model, product.sku):
        replacement = resolve_or_create_product(
            user=user,
            brand_name=brand_name,
            model=model,
            sku=sku,
            product_type_name=product.product_type.name,
            category=product.category,
            duplicate_acknowledged=duplicate_product_acknowledged,
        )
        if (
            replacement.category != product.category
            or replacement.product_type_id != product.product_type_id
            or not replacement.is_active
        ):
            raise ValidationError(
                "Choose an active product in the same category and type as this asset."
            )
        asset.product = replacement
    for field, value in values.items():
        setattr(asset, field, value)
    asset.updated_by = user
    asset.full_clean(exclude=["normalized_serial"])
    asset.save()
    new = {field: getattr(asset, field) for field in EDIT_FIELDS}
    new.update(
        product_id=str(asset.product_id),
        brand=asset.product.brand.name,
        model=asset.product.model,
        sku=asset.product.sku,
    )
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=asset,
        summary="Corrected asset details",
        old_values=old,
        new_values=new,
    )
    if duplicate_count:
        record_event(
            actor=user,
            event_type=AuditEvent.EventType.DUPLICATE_SERIAL_ACKNOWLEDGED,
            obj=asset,
            summary="Acknowledged duplicate serial during asset edit",
            metadata={"matched_ids": [str(match.pk) for match in matches]},
        )
    return asset
