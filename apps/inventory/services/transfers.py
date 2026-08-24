from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role
from apps.locations.scoping import require_location_access

from ..models import MovementType, UnitAsset
from ..transitions import validate_transferable
from .ledger import adjust_balance, create_transaction_header, write_quantity_line, write_unit_line


@transaction.atomic
def bulk_transfer(
    *,
    user,
    destination_location,
    occurred_at,
    unit_asset_ids=None,
    quantity_lines=None,
    notes="",
):
    """Location transfer — spec §9 "Bulk location transfer", acceptance
    criterion §21.4 (multiple assets, one transaction). `quantity_lines` is a
    list of {"product": Product, "source_location": Location, "quantity": int}.
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    require_location_access(user, destination_location)
    if not destination_location.is_active:
        raise ValidationError("Cannot transfer stock into an inactive location.")

    unit_asset_ids = list(unit_asset_ids or [])
    quantity_lines = quantity_lines or []
    if not unit_asset_ids and not quantity_lines:
        raise ValidationError("Select at least one item to transfer.")

    # Lock in a stable order (by pk) to avoid deadlocks against concurrent
    # transactions touching overlapping assets (doc 03's multi-line rules).
    assets = list(
        UnitAsset.objects.select_for_update().filter(pk__in=unit_asset_ids).order_by("pk")
    )
    if len(assets) != len(set(unit_asset_ids)):
        raise ValidationError("One or more selected assets could not be found.")

    for asset in assets:
        require_location_access(user, asset.current_location)
        validate_transferable(asset.status)

    for entry in quantity_lines:
        require_location_access(user, entry["source_location"])
        if entry["quantity"] <= 0:
            raise ValidationError("Transfer quantity must be positive.")

    txn = create_transaction_header(
        movement_type=MovementType.TRANSFER,
        performed_by=user,
        occurred_at=occurred_at,
        destination_location=destination_location,
        notes=notes,
    )

    line_number = 1
    for asset in assets:
        write_unit_line(
            transaction=txn,
            line_number=line_number,
            asset=asset,
            to_status=asset.status,
            to_location=destination_location,
            user=user,
        )
        line_number += 1

    for entry in quantity_lines:
        product, source_location, quantity = (
            entry["product"],
            entry["source_location"],
            entry["quantity"],
        )
        adjust_balance(product=product, location=source_location, delta=-quantity)
        adjust_balance(product=product, location=destination_location, delta=quantity)
        write_quantity_line(
            transaction=txn,
            line_number=line_number,
            product=product,
            quantity_delta=quantity,
            from_location=source_location,
            to_location=destination_location,
            notes=notes,
        )
        line_number += 1

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.MOVEMENT_COMPLETED,
        obj=txn,
        summary=(
            f"Transferred {len(assets)} asset(s) and {len(quantity_lines)} quantity line(s) "
            f"to {destination_location}"
        ),
    )
    return txn
