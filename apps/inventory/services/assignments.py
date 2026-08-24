from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role
from apps.locations.scoping import require_location_access

from ..models import MovementType, UnitAsset, UnitStatus
from ..transitions import validate_unit_transition
from .ledger import adjust_balance, create_transaction_header, write_quantity_line, write_unit_line


def _issue_stock(
    *,
    user,
    movement_type,
    to_status,
    occurred_at,
    unit_asset_ids=None,
    quantity_lines=None,
    project_reference="",
    final_customer="",
    employee_name="",
    is_temporary_assignment=None,
    expected_return_date=None,
    condition=None,
    accessories=None,
    notes="",
):
    """Shared by assign_to_employee() and deliver_to_customer() — both remove
    stock from storage (spec §9, acceptance criterion §21.6: multiple unit
    and quantity lines in one transaction). `quantity_lines` is a list of
    {"product": Product, "location": Location, "quantity": int}.
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)

    unit_asset_ids = list(unit_asset_ids or [])
    quantity_lines = quantity_lines or []
    if not unit_asset_ids and not quantity_lines:
        raise ValidationError("Select at least one item.")

    assets = list(
        UnitAsset.objects.select_for_update().filter(pk__in=unit_asset_ids).order_by("pk")
    )
    if len(assets) != len(set(unit_asset_ids)):
        raise ValidationError("One or more selected assets could not be found.")

    for asset in assets:
        require_location_access(user, asset.current_location)
        validate_unit_transition(asset.status, to_status)

    for entry in quantity_lines:
        require_location_access(user, entry["location"])
        if entry["quantity"] <= 0:
            raise ValidationError("Quantity must be positive.")

    txn = create_transaction_header(
        movement_type=movement_type,
        performed_by=user,
        occurred_at=occurred_at,
        project_reference=project_reference,
        final_customer=final_customer,
        employee_name=employee_name,
        is_temporary_assignment=is_temporary_assignment,
        expected_return_date=expected_return_date,
        notes=notes,
    )

    line_number = 1
    for asset in assets:
        write_unit_line(
            transaction=txn,
            line_number=line_number,
            asset=asset,
            to_status=to_status,
            to_location=None,
            user=user,
            condition=condition,
            accessories=accessories,
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
            f"{len(quantity_lines)} quantity line(s)"
        ),
    )
    return txn


@transaction.atomic
def assign_to_employee(
    *,
    user,
    employee_name,
    occurred_at,
    unit_asset_ids=None,
    quantity_lines=None,
    project_reference="",
    is_temporary_assignment=None,
    expected_return_date=None,
    condition=None,
    accessories=None,
    notes="",
):
    if not employee_name:
        raise ValidationError("Employee name is required for an assignment.")

    return _issue_stock(
        user=user,
        movement_type=MovementType.ASSIGNMENT,
        to_status=UnitStatus.ASSIGNED,
        occurred_at=occurred_at,
        unit_asset_ids=unit_asset_ids,
        quantity_lines=quantity_lines,
        project_reference=project_reference,
        employee_name=employee_name,
        is_temporary_assignment=is_temporary_assignment,
        expected_return_date=expected_return_date,
        condition=condition,
        accessories=accessories,
        notes=notes,
    )


@transaction.atomic
def deliver_to_customer(
    *,
    user,
    final_customer,
    occurred_at,
    unit_asset_ids=None,
    quantity_lines=None,
    project_reference="",
    condition=None,
    accessories=None,
    notes="",
):
    if not final_customer:
        raise ValidationError("Final customer is required for a delivery.")

    return _issue_stock(
        user=user,
        movement_type=MovementType.DELIVERY,
        to_status=UnitStatus.DELIVERED,
        occurred_at=occurred_at,
        unit_asset_ids=unit_asset_ids,
        quantity_lines=quantity_lines,
        project_reference=project_reference,
        final_customer=final_customer,
        condition=condition,
        accessories=accessories,
        notes=notes,
    )
