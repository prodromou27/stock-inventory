from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role
from apps.locations.scoping import require_location_access

from ..models import MovementType, ReservationStatus, StockReservation, UnitAsset, UnitStatus
from ..transitions import validate_unit_transition
from .ledger import adjust_reserved, create_transaction_header, write_unit_line


@transaction.atomic
def reserve_stock(
    *,
    user,
    occurred_at,
    project_reference,
    final_customer="",
    unit_asset_ids=None,
    quantity_lines=None,
    notes="",
):
    """Project/customer reservation — spec §9 "Reserve stock", acceptance
    criterion §21.5. Stock stays physically in place; unit assets move to
    Reserved status, quantity lines get a StockReservation row (doc 02/03 —
    the reservation itself, not an InventoryTransactionLine, is what tracks
    the reserved quantity). `quantity_lines` is a list of
    {"product": Product, "location": Location, "quantity": int}.
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    if not project_reference:
        raise ValidationError("Project reference is required to reserve stock.")

    unit_asset_ids = list(unit_asset_ids or [])
    quantity_lines = quantity_lines or []
    if not unit_asset_ids and not quantity_lines:
        raise ValidationError("Select at least one item to reserve.")

    assets = list(
        UnitAsset.objects.select_for_update().filter(pk__in=unit_asset_ids).order_by("pk")
    )
    if len(assets) != len(set(unit_asset_ids)):
        raise ValidationError("One or more selected assets could not be found.")

    for asset in assets:
        require_location_access(user, asset.current_location)
        validate_unit_transition(asset.status, UnitStatus.RESERVED)

    for entry in quantity_lines:
        require_location_access(user, entry["location"])
        if entry["quantity"] <= 0:
            raise ValidationError("Reservation quantity must be positive.")

    txn = create_transaction_header(
        movement_type=MovementType.RESERVATION,
        performed_by=user,
        occurred_at=occurred_at,
        project_reference=project_reference,
        final_customer=final_customer,
        notes=notes,
    )

    line_number = 1
    for asset in assets:
        write_unit_line(
            transaction=txn,
            line_number=line_number,
            asset=asset,
            to_status=UnitStatus.RESERVED,
            to_location=asset.current_location,
            user=user,
        )
        line_number += 1

    for entry in quantity_lines:
        product, location, quantity = entry["product"], entry["location"], entry["quantity"]
        adjust_reserved(product=product, location=location, delta=quantity)
        StockReservation.objects.create(
            product=product,
            location=location,
            quantity=quantity,
            project_reference=project_reference,
            final_customer=final_customer,
            status=ReservationStatus.ACTIVE,
            created_by=user,
            reservation_transaction=txn,
        )

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.MOVEMENT_COMPLETED,
        obj=txn,
        summary=(
            f"Reserved {len(assets)} asset(s) and {len(quantity_lines)} quantity line(s) "
            f"for '{project_reference}'"
        ),
    )
    return txn


@transaction.atomic
def release_reservation(*, user, occurred_at, unit_asset_ids=None, reservations=None, notes=""):
    """Reservation release — returns unit assets to In Stock and marks
    quantity StockReservation rows released, freeing the reserved quantity.
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)

    unit_asset_ids = list(unit_asset_ids or [])
    reservations = list(reservations or [])
    if not unit_asset_ids and not reservations:
        raise ValidationError("Select at least one item to release.")

    assets = list(
        UnitAsset.objects.select_for_update().filter(pk__in=unit_asset_ids).order_by("pk")
    )
    if len(assets) != len(set(unit_asset_ids)):
        raise ValidationError("One or more selected assets could not be found.")

    for asset in assets:
        require_location_access(user, asset.current_location)
        validate_unit_transition(asset.status, UnitStatus.IN_STOCK)

    for reservation in reservations:
        require_location_access(user, reservation.location)
        if reservation.status != ReservationStatus.ACTIVE:
            raise ValidationError(f"Reservation {reservation.pk} is not active.")

    txn = create_transaction_header(
        movement_type=MovementType.RESERVATION_RELEASE,
        performed_by=user,
        occurred_at=occurred_at,
        notes=notes,
    )

    line_number = 1
    for asset in assets:
        write_unit_line(
            transaction=txn,
            line_number=line_number,
            asset=asset,
            to_status=UnitStatus.IN_STOCK,
            to_location=asset.current_location,
            user=user,
        )
        line_number += 1

    for reservation in reservations:
        adjust_reserved(
            product=reservation.product, location=reservation.location, delta=-reservation.quantity
        )
        reservation.status = ReservationStatus.RELEASED
        reservation.save(update_fields=["status"])

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.MOVEMENT_COMPLETED,
        obj=txn,
        summary=f"Released {len(assets)} asset(s) and {len(reservations)} reservation(s)",
    )
    return txn
