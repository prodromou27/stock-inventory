from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role
from apps.locations.scoping import require_location_access

from ..access import require_asset_access
from ..models import MovementType, UnitAsset, UnitStatus
from ..transitions import validate_unit_transition
from .ledger import adjust_balance, create_transaction_header, write_quantity_line, write_unit_line

# Removal Date applies only to movements that take an item out of usable
# storage — mark_damaged leaves the asset in place (doc 03, fixed after an
# earlier internal inconsistency in that doc).
_REMOVES_FROM_STORAGE = {MovementType.MARK_LOST, MovementType.DISPOSAL}


@transaction.atomic
def _change_unit_and_quantity_status(
    *,
    user,
    movement_type,
    to_status,
    occurred_at,
    unit_asset_ids=None,
    quantity_lines=None,
    notes="",
    wipe_method="",
    witness_name="",
):
    """Shared by mark_damaged()/mark_lost()/dispose() — spec §9 "Damage, loss,
    and disposal". `quantity_lines` is a list of
    {"product": Product, "location": Location, "quantity": int}.
    `wipe_method`/`witness_name` are disposal-only (see dispose()'s docstring)
    but plumbed through generically since only create_transaction_header()
    needs to know about them.
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)

    unit_asset_ids = list(unit_asset_ids or [])
    quantity_lines = quantity_lines or []
    if not unit_asset_ids and not quantity_lines:
        raise ValidationError("Select at least one item.")
    if not notes:
        raise ValidationError("A reason/notes value is required.")

    assets = list(
        UnitAsset.objects.select_for_update().filter(pk__in=unit_asset_ids).order_by("pk")
    )
    if len(assets) != len(set(unit_asset_ids)):
        raise ValidationError("One or more selected assets could not be found.")

    for asset in assets:
        require_asset_access(user, asset)
        validate_unit_transition(asset.status, to_status)

    for entry in quantity_lines:
        require_location_access(user, entry["location"])
        if entry["quantity"] <= 0:
            raise ValidationError("Quantity must be positive.")

    txn = create_transaction_header(
        movement_type=movement_type,
        performed_by=user,
        occurred_at=occurred_at,
        notes=notes,
        wipe_method=wipe_method,
        witness_name=witness_name,
    )

    removes_from_storage = movement_type in _REMOVES_FROM_STORAGE
    line_number = 1
    for asset in assets:
        to_location = None if removes_from_storage else asset.current_location
        write_unit_line(
            transaction=txn,
            line_number=line_number,
            asset=asset,
            to_status=to_status,
            to_location=to_location,
            user=user,
            notes=notes,
        )
        line_number += 1

    for entry in quantity_lines:
        product, location, quantity = entry["product"], entry["location"], entry["quantity"]
        adjust_balance(product=product, location=location, delta=-quantity)
        write_quantity_line(
            transaction=txn,
            line_number=line_number,
            product=product,
            quantity_delta=-quantity,
            from_location=location,
            to_location=None,
            notes=notes,
        )
        line_number += 1

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.MOVEMENT_COMPLETED,
        obj=txn,
        summary=(
            f"{txn.get_movement_type_display()}: {len(assets)} asset(s) and "
            f"{len(quantity_lines)} quantity line(s) — {notes}"
        ),
    )
    return txn


def mark_damaged(*, user, occurred_at, unit_asset_ids=None, quantity_lines=None, notes=""):
    return _change_unit_and_quantity_status(
        user=user,
        movement_type=MovementType.MARK_DAMAGED,
        to_status=UnitStatus.DAMAGED,
        occurred_at=occurred_at,
        unit_asset_ids=unit_asset_ids,
        quantity_lines=quantity_lines,
        notes=notes,
    )


def mark_lost(*, user, occurred_at, unit_asset_ids=None, quantity_lines=None, notes=""):
    return _change_unit_and_quantity_status(
        user=user,
        movement_type=MovementType.MARK_LOST,
        to_status=UnitStatus.LOST,
        occurred_at=occurred_at,
        unit_asset_ids=unit_asset_ids,
        quantity_lines=quantity_lines,
        notes=notes,
    )


def dispose(
    *,
    user,
    occurred_at,
    unit_asset_ids=None,
    quantity_lines=None,
    notes="",
    wipe_method="",
    witness_name="",
):
    """`wipe_method`/`witness_name` back the disposal certificate
    (apps.documents.pdf's generic document skeleton renders them only when
    present — see build_document_context()) — captured here, on the
    append-only transaction header, the same way every other movement-type-
    specific optional field already is (employee_name for assignment,
    final_customer for delivery, ...), not a separate model.
    """
    return _change_unit_and_quantity_status(
        user=user,
        movement_type=MovementType.DISPOSAL,
        to_status=UnitStatus.DISPOSED,
        occurred_at=occurred_at,
        unit_asset_ids=unit_asset_ids,
        quantity_lines=quantity_lines,
        notes=notes,
        wipe_method=wipe_method,
        witness_name=witness_name,
    )


@transaction.atomic
def return_repaired_to_stock(*, user, location, occurred_at, unit_asset_ids, notes=""):
    """Record completion of a repair and return damaged units to stock."""
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    require_location_access(user, location)
    if not location.is_active:
        raise ValidationError("Cannot return repaired stock to an inactive location.")

    unit_asset_ids = list(unit_asset_ids or [])
    if not unit_asset_ids:
        raise ValidationError("Select at least one damaged asset.")
    assets = list(
        UnitAsset.objects.select_for_update().filter(pk__in=unit_asset_ids).order_by("pk")
    )
    if len(assets) != len(set(unit_asset_ids)):
        raise ValidationError("One or more selected assets could not be found.")
    for asset in assets:
        require_asset_access(user, asset)
        validate_unit_transition(asset.status, UnitStatus.IN_STOCK)

    txn = create_transaction_header(
        movement_type=MovementType.RETURN_ASSESSMENT,
        performed_by=user,
        occurred_at=occurred_at,
        destination_location=location,
        notes=notes,
    )
    for line_number, asset in enumerate(assets, start=1):
        write_unit_line(
            transaction=txn,
            line_number=line_number,
            asset=asset,
            to_status=UnitStatus.IN_STOCK,
            to_location=location,
            user=user,
            notes=notes,
        )
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.MOVEMENT_COMPLETED,
        obj=txn,
        summary=f"Returned {len(assets)} repaired asset(s) to stock",
    )
    return txn
