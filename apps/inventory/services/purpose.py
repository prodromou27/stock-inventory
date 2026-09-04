"""Stock Purpose (Internal/Customer) reclassification — new on direct
request, not part of the original build spec (see
docs/architecture/09-delivery-backlog.md's dated entry for this wave).
Orthogonal to UnitStatus: reclassifying doesn't move an asset's status or
location, so it never goes through apps.inventory.transitions.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role
from apps.locations.scoping import require_location_access

from ..access import require_asset_access
from ..models import MovementType, StockPurpose, UnitAsset
from .ledger import adjust_balance, create_transaction_header, write_quantity_line


@transaction.atomic
def reclassify_unit_purpose(*, user, unit_asset, new_purpose, occurred_at, reason):
    """Relabels a single serialized asset's Stock Purpose. A label change,
    not a stock movement (the asset doesn't move location or on-hand
    quantity), so no ledger transaction/line is written — consistent with
    how apps.catalog.services.update_product handles non-ledger field edits.
    Always audited via AuditEvent.STOCK_PURPOSE_CHANGED.
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    if not reason:
        raise ValidationError("A reason is required to reclassify stock purpose.")
    if new_purpose not in StockPurpose.values:
        raise ValidationError("Unknown stock purpose.")

    asset = UnitAsset.objects.select_for_update().get(pk=unit_asset.pk)
    require_asset_access(user, asset)
    old_purpose = asset.stock_purpose
    if old_purpose == new_purpose:
        raise ValidationError("Asset is already classified as that stock purpose.")

    asset.stock_purpose = new_purpose
    asset.updated_by = user
    asset.full_clean(exclude=["normalized_serial"])
    asset.save(update_fields=["stock_purpose", "updated_by", "updated_at"])

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.STOCK_PURPOSE_CHANGED,
        obj=asset,
        summary=f"Reclassified {asset} from {old_purpose} to {new_purpose} ({reason})",
        old_values={"stock_purpose": old_purpose},
        new_values={"stock_purpose": new_purpose},
        metadata={"reason": reason, "occurred_at": str(occurred_at)},
    )
    return asset


@transaction.atomic
def reclassify_quantity_purpose(
    *, user, product, location, from_purpose, to_purpose, quantity, occurred_at, reason
):
    """Moves `quantity` of a quantity-tracked product between two Stock
    Purpose buckets at the same location. Unlike the unit case, this *is* a
    real stock movement — two StockBalance rows change — so it's a proper
    audited ledger transaction (MovementType.PURPOSE_CHANGE), symmetric to
    bulk_transfer()'s two-leg shape but changing stock_purpose instead of
    location. adjust_balance()'s existing negative/available-stock guard is
    the only "not enough stock to reclassify" check needed — nothing new.
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    require_location_access(user, location)
    if not reason:
        raise ValidationError("A reason is required to reclassify stock purpose.")
    if from_purpose == to_purpose:
        raise ValidationError("Source and destination stock purpose must differ.")
    if from_purpose not in StockPurpose.values or to_purpose not in StockPurpose.values:
        raise ValidationError("Unknown stock purpose.")
    if not quantity or quantity <= 0:
        raise ValidationError("Quantity must be positive.")

    txn = create_transaction_header(
        movement_type=MovementType.PURPOSE_CHANGE,
        performed_by=user,
        occurred_at=occurred_at,
        source_location=location,
        destination_location=location,
        notes=reason,
    )

    adjust_balance(product=product, location=location, delta=-quantity, stock_purpose=from_purpose)
    adjust_balance(product=product, location=location, delta=quantity, stock_purpose=to_purpose)
    write_quantity_line(
        transaction=txn,
        line_number=1,
        product=product,
        quantity_delta=-quantity,
        from_location=location,
        to_location=location,
        stock_purpose=from_purpose,
        notes=reason,
    )
    write_quantity_line(
        transaction=txn,
        line_number=2,
        product=product,
        quantity_delta=quantity,
        from_location=location,
        to_location=location,
        stock_purpose=to_purpose,
        notes=reason,
    )

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.STOCK_PURPOSE_CHANGED,
        obj=txn,
        summary=(
            f"Reclassified {quantity} x {product} @ {location} from {from_purpose} "
            f"to {to_purpose} ({reason})"
        ),
        old_values={"stock_purpose": from_purpose, "quantity": quantity},
        new_values={"stock_purpose": to_purpose, "quantity": quantity},
    )
    return txn
