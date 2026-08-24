from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, require_role

from ..models import InventoryTransaction, MovementType, StockBalance, UnitAsset
from .ledger import adjust_balance, create_transaction_header, write_quantity_line, write_unit_line


@transaction.atomic
def correct_unit_status(*, user, unit_asset, to_status, occurred_at, reason, to_location=None):
    """Administrator-only. Forces an asset to any status, bypassing the
    normal transition table (spec §8/§12, doc 03) — e.g. recovering a Lost
    asset, or Damaged -> In Stock after repair. Always audited with a reason;
    the original history is preserved, never rewritten.
    """
    require_role(user, ADMINISTRATOR)
    if not reason:
        raise ValidationError("A reason is required for an administrator correction.")

    asset = UnitAsset.objects.select_for_update().get(pk=unit_asset.pk)
    from_status = asset.status
    from_location = asset.current_location
    resolved_location = to_location if to_location is not None else from_location

    txn = create_transaction_header(
        movement_type=MovementType.CORRECTION,
        performed_by=user,
        occurred_at=occurred_at,
        notes=reason,
    )
    write_unit_line(
        transaction=txn,
        line_number=1,
        asset=asset,
        to_status=to_status,
        to_location=resolved_location,
        user=user,
        notes=reason,
    )

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.ADMIN_CORRECTION,
        obj=txn,
        summary=f"Administrator correction: {asset} {from_status} -> {to_status} ({reason})",
        old_values={
            "status": from_status,
            "location": str(from_location) if from_location else None,
        },
        new_values={
            "status": to_status,
            "location": str(resolved_location) if resolved_location else None,
        },
    )
    return txn


@transaction.atomic
def correct_balance(*, user, product, location, new_on_hand_quantity, occurred_at, reason):
    """Administrator-only direct StockBalance.on_hand_quantity adjustment
    (spec §6: "an Administrator performs an explicitly logged correction").
    """
    require_role(user, ADMINISTRATOR)
    if not reason:
        raise ValidationError("A reason is required for an administrator correction.")
    if new_on_hand_quantity < 0:
        raise ValidationError("On-hand quantity cannot be negative even for a correction.")

    balance = StockBalance.objects.select_for_update().get(product=product, location=location)
    old_on_hand = balance.on_hand_quantity
    delta = new_on_hand_quantity - old_on_hand
    if delta == 0:
        raise ValidationError("New quantity is the same as the current quantity.")

    balance.on_hand_quantity = new_on_hand_quantity
    balance.full_clean()
    balance.save()

    txn = create_transaction_header(
        movement_type=MovementType.CORRECTION,
        performed_by=user,
        occurred_at=occurred_at,
        notes=reason,
    )
    write_quantity_line(
        transaction=txn,
        line_number=1,
        product=product,
        quantity_delta=delta,
        from_location=location if delta < 0 else None,
        to_location=location if delta > 0 else None,
        notes=reason,
    )

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.ADMIN_CORRECTION,
        obj=txn,
        summary=(
            f"Administrator correction: {product} @ {location} on-hand "
            f"{old_on_hand} -> {new_on_hand_quantity} ({reason})"
        ),
        old_values={"on_hand_quantity": old_on_hand},
        new_values={"on_hand_quantity": new_on_hand_quantity},
    )
    return txn


@transaction.atomic
def reverse_transaction(*, user, original_transaction, occurred_at, reason):
    """Administrator-only. Undoes the *effect* of a specific completed
    transaction by writing a new transaction that restores every line's
    from_status/from_location — never edits or deletes the original (doc 03).

    Refuses if any touched asset has moved on since (a later movement would
    be silently skipped), and refuses a transaction that's already been
    reversed. Reverses every line in the original transaction — there is no
    partial reversal.
    """
    require_role(user, ADMINISTRATOR)
    if not reason:
        raise ValidationError("A reason is required for a reversal.")

    already_reversed = InventoryTransaction.objects.filter(
        related_transaction=original_transaction, movement_type=MovementType.REVERSAL
    ).exists()
    if already_reversed:
        raise ValidationError("This transaction has already been reversed.")

    lines = list(
        original_transaction.lines.select_related("unit_asset", "product").order_by("line_number")
    )
    if not lines:
        raise ValidationError("This transaction has no lines to reverse.")

    locked_assets = {}
    for line in lines:
        if line.unit_asset_id is not None:
            asset = UnitAsset.objects.select_for_update().get(pk=line.unit_asset_id)
            if asset.status != line.to_status or asset.current_location_id != line.to_location_id:
                raise ValidationError(
                    f"{asset} has changed since transaction "
                    f"{original_transaction.transaction_number} — a later movement occurred. "
                    "Use a correction instead of a reversal."
                )
            locked_assets[line.unit_asset_id] = asset

    txn = create_transaction_header(
        movement_type=MovementType.REVERSAL,
        performed_by=user,
        occurred_at=occurred_at,
        related_transaction=original_transaction,
        notes=reason,
    )

    for line_number, line in enumerate(lines, start=1):
        if line.unit_asset_id is not None:
            asset = locked_assets[line.unit_asset_id]
            write_unit_line(
                transaction=txn,
                line_number=line_number,
                asset=asset,
                to_status=line.from_status,
                to_location=line.from_location,
                user=user,
                notes=reason,
            )
        else:
            reversal_location = line.to_location or line.from_location
            adjust_balance(
                product=line.product,
                location=reversal_location,
                delta=-line.quantity_delta,
                respect_available=False,
            )
            write_quantity_line(
                transaction=txn,
                line_number=line_number,
                product=line.product,
                quantity_delta=-line.quantity_delta,
                from_location=line.to_location,
                to_location=line.from_location,
                notes=reason,
            )

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.ADMIN_REVERSAL,
        obj=txn,
        summary=f"Reversed transaction {original_transaction.transaction_number} ({reason})",
    )
    return txn
