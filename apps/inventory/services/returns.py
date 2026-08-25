from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role
from apps.locations.scoping import require_location_access

from ..access import require_asset_access, require_transaction_access
from ..models import InventoryTransactionLine, MovementType, UnitAsset, UnitStatus
from ..transitions import validate_unit_transition
from .ledger import adjust_balance, create_transaction_header, write_quantity_line, write_unit_line


@transaction.atomic
def return_stock(
    *,
    user,
    original_transaction,
    location,
    occurred_at,
    unit_asset_ids=None,
    quantity_lines=None,
    condition=None,
    accessories=None,
    notes="",
):
    """Partial or complete return against a specific assignment/delivery — a
    *new* transaction referencing the original, containing only the returned
    lines (spec §9 "Partial return", acceptance criterion §21.7). Unreturned
    lines on the original keep their Assigned/Delivered status untouched —
    there is no partial mutation of the original transaction (doc 03).

    `quantity_lines` is a list of {"product": Product, "quantity": int}.
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    original_transaction = original_transaction.__class__.objects.select_for_update().get(
        pk=original_transaction.pk
    )
    require_transaction_access(user, original_transaction)
    require_location_access(user, location)

    if original_transaction.movement_type not in (MovementType.ASSIGNMENT, MovementType.DELIVERY):
        raise ValidationError(
            "Returns can only be recorded against an assignment or delivery transaction."
        )

    unit_asset_ids = list(unit_asset_ids or [])
    quantity_lines = quantity_lines or []
    if not unit_asset_ids and not quantity_lines:
        raise ValidationError("Select at least one item to return.")

    assets = list(
        UnitAsset.objects.select_for_update().filter(pk__in=unit_asset_ids).order_by("pk")
    )
    if len(assets) != len(set(unit_asset_ids)):
        raise ValidationError("One or more selected assets could not be found.")

    original_asset_ids = set(
        InventoryTransactionLine.objects.filter(
            transaction=original_transaction, unit_asset__isnull=False
        ).values_list("unit_asset_id", flat=True)
    )
    for asset in assets:
        require_asset_access(user, asset)
        if asset.pk not in original_asset_ids:
            raise ValidationError(
                f"{asset} was not part of transaction {original_transaction.transaction_number}."
            )
        latest_line = asset.transaction_lines.order_by(
            "-transaction__created_at", "-line_number"
        ).first()
        if latest_line is None or latest_line.transaction_id != original_transaction.pk:
            raise ValidationError(
                f"{asset} is no longer outstanding on "
                f"{original_transaction.transaction_number}."
            )
        validate_unit_transition(asset.status, UnitStatus.RETURNED)

    for entry in quantity_lines:
        if entry["quantity"] <= 0:
            raise ValidationError("Return quantity must be positive.")
        product = entry["product"]
        issued = -(
            original_transaction.lines.filter(unit_asset=None, product=product).aggregate(
                total=Sum("quantity_delta")
            )["total"]
            or 0
        )
        reversed_return_ids = original_transaction.related_transactions.filter(
            movement_type=MovementType.RETURN,
            related_transactions__movement_type=MovementType.REVERSAL,
        ).values_list("pk", flat=True)
        returned = (
            InventoryTransactionLine.objects.filter(
                transaction__related_transaction=original_transaction,
                transaction__movement_type=MovementType.RETURN,
                unit_asset=None,
                product=product,
            )
            .exclude(transaction_id__in=reversed_return_ids)
            .aggregate(total=Sum("quantity_delta"))["total"]
            or 0
        )
        if not issued or returned + entry["quantity"] > issued:
            raise ValidationError(
                f"Return quantity for {product} exceeds the {issued - returned} outstanding."
            )

    txn = create_transaction_header(
        movement_type=MovementType.RETURN,
        performed_by=user,
        occurred_at=occurred_at,
        destination_location=location,
        related_transaction=original_transaction,
        project_reference=original_transaction.project_reference,
        final_customer=original_transaction.final_customer,
        notes=notes,
    )

    line_number = 1
    for asset in assets:
        write_unit_line(
            transaction=txn,
            line_number=line_number,
            asset=asset,
            to_status=UnitStatus.RETURNED,
            to_location=location,
            user=user,
            condition=condition,
            accessories=accessories,
            notes=notes,
        )
        line_number += 1

    for entry in quantity_lines:
        product, quantity = entry["product"], entry["quantity"]
        adjust_balance(product=product, location=location, delta=quantity)
        write_quantity_line(
            transaction=txn,
            line_number=line_number,
            product=product,
            quantity_delta=quantity,
            from_location=None,
            to_location=location,
            notes=notes,
        )
        line_number += 1

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.MOVEMENT_COMPLETED,
        obj=txn,
        summary=(
            f"Returned {len(assets)} asset(s) and {len(quantity_lines)} quantity line(s) "
            f"against {original_transaction.transaction_number}"
        ),
    )
    return txn


@transaction.atomic
def assess_return(*, user, to_status, occurred_at, unit_asset_ids, notes=""):
    """Return assessment — resolves a Returned asset to In Stock, Damaged, or
    Disposed (spec §9). Quantity returns have no per-unit "awaiting
    assessment" state (doc 10's open item #6), so this only applies to units.
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)

    if to_status not in (UnitStatus.IN_STOCK, UnitStatus.DAMAGED, UnitStatus.DISPOSED):
        raise ValidationError("Return assessment must resolve to In Stock, Damaged, or Disposed.")

    unit_asset_ids = list(unit_asset_ids or [])
    if not unit_asset_ids:
        raise ValidationError("Select at least one asset to assess.")

    assets = list(
        UnitAsset.objects.select_for_update().filter(pk__in=unit_asset_ids).order_by("pk")
    )
    if len(assets) != len(set(unit_asset_ids)):
        raise ValidationError("One or more selected assets could not be found.")

    for asset in assets:
        require_location_access(user, asset.current_location)
        validate_unit_transition(asset.status, to_status)

    txn = create_transaction_header(
        movement_type=MovementType.RETURN_ASSESSMENT,
        performed_by=user,
        occurred_at=occurred_at,
        notes=notes,
    )

    for line_number, asset in enumerate(assets, start=1):
        write_unit_line(
            transaction=txn,
            line_number=line_number,
            asset=asset,
            to_status=to_status,
            to_location=asset.current_location,
            user=user,
            notes=notes,
        )

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.MOVEMENT_COMPLETED,
        obj=txn,
        summary=f"Assessed {len(assets)} returned asset(s) as {to_status}",
    )
    return txn
