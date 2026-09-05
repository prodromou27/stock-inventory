from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, require_role

from ..models import (
    InventoryTransaction,
    MovementType,
    ReservationStatus,
    StockBalance,
    StockPurpose,
    StockReservation,
    UnitAsset,
    UnitStatus,
)
from .ledger import (
    adjust_balance,
    adjust_reserved,
    create_transaction_header,
    write_quantity_line,
    write_reservation_line,
    write_unit_line,
)


@transaction.atomic
def correct_unit_status(
    *, user, unit_asset, to_status, occurred_at, reason, to_location=None, arrival_date=None
):
    """Administrator-only. Forces an asset to any status, bypassing the
    normal transition table (spec §8/§12, doc 03) — e.g. recovering a Lost
    asset, or Damaged -> In Stock after repair. Always audited with a reason;
    the original history is preserved, never rewritten.

    `arrival_date`, when given, is the one path to change a UnitAsset's
    Arrival Date after receipt — ordinary editing never touches it; this is
    the audited correction. Independent of `occurred_at` (the date this
    correction itself took effect) and of created_at/created_by (who/when the
    row was inserted) — three distinct, never-conflated timestamps.
    """
    require_role(user, ADMINISTRATOR)
    if not reason:
        raise ValidationError("A reason is required for an administrator correction.")

    asset = UnitAsset.objects.select_for_update().get(pk=unit_asset.pk)
    from_status = asset.status
    from_location = asset.current_location
    from_arrival_date = asset.arrival_date
    if arrival_date is not None:
        asset.arrival_date = arrival_date
    resolved_location = to_location if to_location is not None else from_location
    requires_location = {UnitStatus.IN_STOCK, UnitStatus.RESERVED, UnitStatus.RETURNED}
    requires_no_location = {
        UnitStatus.ASSIGNED,
        UnitStatus.DELIVERED,
        UnitStatus.LOST,
        UnitStatus.DISPOSED,
    }
    if to_status in requires_location and resolved_location is None:
        raise ValidationError(f"Status '{UnitStatus(to_status).label}' requires a location.")
    if to_status in requires_no_location and to_location is not None:
        raise ValidationError(f"Status '{UnitStatus(to_status).label}' cannot have a location.")
    if to_status in requires_no_location:
        resolved_location = None

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

    old_values = {
        "status": from_status,
        "location": str(from_location) if from_location else None,
    }
    new_values = {
        "status": to_status,
        "location": str(resolved_location) if resolved_location else None,
    }
    if arrival_date is not None:
        old_values["arrival_date"] = from_arrival_date.isoformat() if from_arrival_date else None
        new_values["arrival_date"] = arrival_date.isoformat()

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.ADMIN_CORRECTION,
        obj=txn,
        summary=f"Administrator correction: {asset} {from_status} -> {to_status} ({reason})",
        old_values=old_values,
        new_values=new_values,
    )
    return txn


@transaction.atomic
def correct_balance(
    *,
    user,
    product,
    location,
    new_on_hand_quantity,
    occurred_at,
    reason,
    stock_purpose=StockPurpose.INTERNAL,
):
    """Administrator-only direct StockBalance.on_hand_quantity adjustment
    (spec §6: "an Administrator performs an explicitly logged correction").
    """
    require_role(user, ADMINISTRATOR)
    if not reason:
        raise ValidationError("A reason is required for an administrator correction.")
    if new_on_hand_quantity < 0:
        raise ValidationError("On-hand quantity cannot be negative even for a correction.")

    balance = StockBalance.objects.select_for_update().get(
        product=product, location=location, stock_purpose=stock_purpose
    )
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
        stock_purpose=stock_purpose,
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
def correct_reference_fields(
    *, user, unit_asset, reason, project_reference=None, final_customer=None
):
    """Administrator-only. Directly edits project_reference/final_customer
    — a plain descriptive-field correction, not a ledger movement (status/
    location are untouched), used by apps.dataquality's "customer stock
    missing customer/project reference" finding. Unlike the ordinary inline
    grid-field edit (apps.inventory.views.AssetGridFieldUpdateView), this
    requires a reason and is always an ADMIN_CORRECTION audit event — a
    correction taken from the Data Quality Centre gets the same
    accountability trail as every other administrator correction, not the
    lighter-weight RECORD_UPDATED a routine edit gets.
    """
    require_role(user, ADMINISTRATOR)
    if not reason:
        raise ValidationError("A reason is required for an administrator correction.")

    asset = UnitAsset.objects.select_for_update().get(pk=unit_asset.pk)
    old_values = {
        "project_reference": asset.project_reference,
        "final_customer": asset.final_customer,
    }
    if project_reference is not None:
        asset.project_reference = project_reference
    if final_customer is not None:
        asset.final_customer = final_customer
    asset.updated_by = user
    asset.full_clean(exclude=["normalized_serial"])
    asset.save()

    new_values = {
        "project_reference": asset.project_reference,
        "final_customer": asset.final_customer,
    }
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.ADMIN_CORRECTION,
        obj=asset,
        summary=f"Administrator correction: {asset} reference fields updated ({reason})",
        old_values=old_values,
        new_values=new_values,
    )
    return asset


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

    # Reservation-release lines are processed after every other line, not in
    # original line_number order: `_issue_stock` writes a reservation-release
    # line *before* the quantity-deduction line for the same entry
    # (assignments.py's _consume_matching_reservations() runs first), so
    # reversing in line_number order would restore `reserved` before
    # `on_hand` — and adjust_reserved() correctly refuses reserved >
    # on_hand. Restoring every balance/unit line first guarantees on_hand is
    # back to its pre-transaction level before any reserved amount is
    # restored against it, so a reservation-consuming assignment/delivery
    # can always be reversed.
    reservation_lines = []
    other_lines = []
    for line in lines:
        if line.unit_asset_id is None and line.stock_reservation_id:
            reservation_lines.append(line)
        else:
            other_lines.append(line)

    line_number = 0
    for line in other_lines:
        line_number += 1
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
            _recompute_last_removal_date(asset)
            continue
        if (
            original_transaction.movement_type == MovementType.TRANSFER
            and line.from_location_id
            and line.to_location_id
        ):
            adjust_balance(
                product=line.product,
                location=line.to_location,
                delta=-line.quantity_delta,
                stock_purpose=line.stock_purpose_snapshot,
                respect_available=False,
            )
            adjust_balance(
                product=line.product,
                location=line.from_location,
                delta=line.quantity_delta,
                stock_purpose=line.stock_purpose_snapshot,
                respect_available=False,
            )
            write_quantity_line(
                transaction=txn,
                line_number=line_number,
                product=line.product,
                quantity_delta=-line.quantity_delta,
                from_location=line.to_location,
                to_location=line.from_location,
                stock_purpose=line.stock_purpose_snapshot,
                notes=reason,
            )
            continue
        reversal_location = line.to_location or line.from_location
        adjust_balance(
            product=line.product,
            location=reversal_location,
            delta=-line.quantity_delta,
            stock_purpose=line.stock_purpose_snapshot,
            respect_available=False,
        )
        write_quantity_line(
            transaction=txn,
            line_number=line_number,
            product=line.product,
            quantity_delta=-line.quantity_delta,
            from_location=line.to_location,
            to_location=line.from_location,
            stock_purpose=line.stock_purpose_snapshot,
            notes=reason,
        )

    for line in reservation_lines:
        line_number += 1
        reservation = StockReservation.objects.select_for_update().get(pk=line.stock_reservation_id)
        adjust_reserved(
            product=line.product,
            location=reservation.location,
            delta=-line.reserved_quantity_delta,
            stock_purpose=line.stock_purpose_snapshot,
        )
        reservation.status = (
            ReservationStatus.RELEASED
            if line.reserved_quantity_delta > 0
            else ReservationStatus.ACTIVE
        )
        update_fields = ["status"]
        if original_transaction.movement_type in (
            MovementType.ASSIGNMENT,
            MovementType.DELIVERY,
        ):
            reservation.consumed_quantity -= abs(line.reserved_quantity_delta)
            reservation.status = ReservationStatus.ACTIVE
            update_fields.append("consumed_quantity")
            if reservation.consuming_transaction_id == original_transaction.pk:
                reservation.consuming_transaction = None
                update_fields.append("consuming_transaction")
        reservation.save(update_fields=update_fields)
        write_reservation_line(
            transaction=txn,
            line_number=line_number,
            reservation=reservation,
            reserved_quantity_delta=-line.reserved_quantity_delta,
            notes=reason,
        )

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.ADMIN_REVERSAL,
        obj=txn,
        summary=f"Reversed transaction {original_transaction.transaction_number} ({reason})",
    )
    return txn


def _recompute_last_removal_date(asset):
    removal_types = {
        MovementType.ASSIGNMENT,
        MovementType.DELIVERY,
        MovementType.MARK_LOST,
        MovementType.DISPOSAL,
        MovementType.CORRECTION,
    }
    latest = (
        asset.transaction_lines.filter(
            transaction__movement_type__in=removal_types,
            from_location__isnull=False,
            to_location__isnull=True,
        )
        .exclude(transaction__related_transactions__movement_type=MovementType.REVERSAL)
        .order_by("-transaction__occurred_at", "-transaction__created_at")
        .values_list("transaction__occurred_at", flat=True)
        .first()
    )
    asset.last_removal_date = latest
    asset.save(update_fields=["last_removal_date", "updated_at"])
