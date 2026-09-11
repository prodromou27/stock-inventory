from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.catalog.models import TrackingMethod
from apps.core.authorization import ADMINISTRATOR, require_role
from apps.locations.models import Location
from apps.locations.scoping import (
    accessible_locations,
    require_location_access,
)

from ..models import ProductLocationThreshold


def location_reorder_settings_for(*, user):
    """Return overrides only for locations inside the caller's live scope."""
    return ProductLocationThreshold.objects.filter(
        location__in=accessible_locations(user)
    ).select_related("product__brand", "product__product_type", "location")


@transaction.atomic
def update_location_reorder_settings(
    *,
    user,
    product,
    location,
    low_stock_threshold,
    target_stock_level,
    min_reorder_quantity,
    preferred_supplier,
):
    require_role(user, ADMINISTRATOR)
    require_location_access(user, location)
    if product.tracking_method == TrackingMethod.UNIT and location.level != Location.Level.COUNTRY:
        raise ValidationError("Unit-tracked asset thresholds must be configured per country.")
    if (
        product.tracking_method == TrackingMethod.QUANTITY
        and location.level == Location.Level.COUNTRY
    ):
        raise ValidationError("Counted-stock overrides must use a Storage Room or Shelf/Rack.")

    override = (
        ProductLocationThreshold.objects.select_for_update()
        .filter(product=product, location=location)
        .first()
    )
    created = override is None
    if created:
        override = ProductLocationThreshold(
            product=product, location=location, created_by=user, updated_by=user
        )
        old_values = {}
    else:
        old_values = {
            "low_stock_threshold": override.low_stock_threshold,
            "target_stock_level": override.target_stock_level,
            "min_reorder_quantity": override.min_reorder_quantity,
            "preferred_supplier": override.preferred_supplier,
        }
    override.low_stock_threshold = low_stock_threshold
    override.target_stock_level = target_stock_level
    override.min_reorder_quantity = min_reorder_quantity
    override.preferred_supplier = preferred_supplier.strip()
    override.updated_by = user
    override.full_clean()
    override.save()
    new_values = {
        "low_stock_threshold": override.low_stock_threshold,
        "target_stock_level": override.target_stock_level,
        "min_reorder_quantity": override.min_reorder_quantity,
        "preferred_supplier": override.preferred_supplier,
    }
    record_event(
        actor=user,
        event_type=(
            AuditEvent.EventType.RECORD_CREATED if created else AuditEvent.EventType.RECORD_UPDATED
        ),
        obj=override,
        summary=f"Saved reorder override for '{product}' at '{location}'",
        old_values=old_values,
        new_values=new_values,
        metadata={"product_id": str(product.pk), "location_id": str(location.pk)},
    )
    return override


@transaction.atomic
def reset_location_reorder_settings(*, user, override):
    require_role(user, ADMINISTRATOR)
    require_location_access(user, override.location)
    snapshot = {
        "product_id": str(override.product_id),
        "location_id": str(override.location_id),
        "low_stock_threshold": override.low_stock_threshold,
        "target_stock_level": override.target_stock_level,
        "min_reorder_quantity": override.min_reorder_quantity,
        "preferred_supplier": override.preferred_supplier,
    }
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=override,
        summary=f"Reset reorder override for '{override.product}' at '{override.location}'",
        old_values=snapshot,
        new_values={"uses_global_settings": True},
    )
    override.delete()
