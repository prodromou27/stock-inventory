from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role

from ..access import scope_asset_queryset
from ..models import UnitAsset
from .transfers import bulk_transfer

EDITABLE_FIELDS = {"supplier", "invoice_number", "project_reference", "notes"}


@transaction.atomic
def bulk_edit_assets(*, user, asset_ids, occurred_at, destination_location=None, updates=None):
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    asset_ids = list(dict.fromkeys(asset_ids))
    if not asset_ids:
        raise ValidationError("Select at least one asset.")
    updates = updates or {}
    if set(updates) - EDITABLE_FIELDS:
        raise ValidationError("One or more fields cannot be edited in bulk.")
    assets = list(
        scope_asset_queryset(
            user,
            UnitAsset.objects.select_for_update(of=("self",))
            .filter(pk__in=asset_ids)
            .select_related("product", "current_location"),
        )
    )
    if len(assets) != len(asset_ids):
        raise PermissionDenied("One or more selected assets are outside your access.")

    for asset in assets:
        old_values = {field: getattr(asset, field) for field in updates}
        changed_fields = []
        for field, value in updates.items():
            if getattr(asset, field) != value:
                setattr(asset, field, value)
                changed_fields.append(field)
        if changed_fields:
            asset.updated_by = user
            asset.full_clean(exclude=["normalized_vendor_serial"])
            asset.save(update_fields=[*changed_fields, "updated_by", "updated_at"])
            record_event(
                actor=user,
                event_type=AuditEvent.EventType.RECORD_UPDATED,
                obj=asset,
                summary=f"Bulk-updated asset '{asset}'",
                old_values=old_values,
                new_values={field: getattr(asset, field) for field in updates},
                metadata={"bulk_edit": True},
            )

    transfer = None
    if destination_location is not None:
        transfer = bulk_transfer(
            user=user,
            destination_location=destination_location,
            occurred_at=occurred_at,
            unit_asset_ids=asset_ids,
            quantity_lines=[],
            notes="Bulk asset location update",
        )
    return assets, transfer
