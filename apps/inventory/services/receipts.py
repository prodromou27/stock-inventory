from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.catalog.models import TrackingMethod
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role
from apps.locations.scoping import require_location_access

from ..models import (
    AssetStatusHistory,
    Condition,
    InventoryTransactionLine,
    MovementType,
    StockBalance,
    UnitAsset,
    UnitStatus,
)
from .duplicates import check_duplicate_serial
from .ledger import create_transaction_header


class DuplicateSerialError(Exception):
    """Raised when a receive_stock() call finds serial matches and the caller
    hasn't set duplicate_serial_acknowledged=True. Carries the matches so the
    view can show them (docs/architecture/05-tracking-and-duplicates.md).
    """

    def __init__(self, matches):
        self.matches = list(matches)
        super().__init__("A unit asset with a matching serial already exists.")


@transaction.atomic
def receive_stock(
    *,
    user,
    product,
    location,
    occurred_at,
    vendor_serial="",
    quantity=None,
    project_reference="",
    final_customer="",
    supplier="",
    invoice_number="",
    condition=Condition.UNKNOWN,
    accessories="",
    notes="",
    duplicate_serial_acknowledged=False,
):
    """Receipt into stock — one InventoryTransaction with one line, for
    either tracking method (spec §9 "Receive stock", acceptance criterion §21.1).
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    require_location_access(user, location)

    if not product.is_active:
        raise ValidationError("Cannot receive stock for an inactive product.")

    if product.tracking_method == TrackingMethod.UNIT:
        return _receive_unit(
            user=user,
            product=product,
            location=location,
            occurred_at=occurred_at,
            vendor_serial=vendor_serial,
            project_reference=project_reference,
            final_customer=final_customer,
            supplier=supplier,
            invoice_number=invoice_number,
            condition=condition,
            accessories=accessories,
            notes=notes,
            duplicate_serial_acknowledged=duplicate_serial_acknowledged,
        )

    return _receive_quantity(
        user=user,
        product=product,
        location=location,
        occurred_at=occurred_at,
        quantity=quantity,
        project_reference=project_reference,
        final_customer=final_customer,
        supplier=supplier,
        invoice_number=invoice_number,
        notes=notes,
    )


def _receive_unit(
    *,
    user,
    product,
    location,
    occurred_at,
    vendor_serial,
    project_reference,
    final_customer,
    supplier,
    invoice_number,
    condition,
    accessories,
    notes,
    duplicate_serial_acknowledged,
):
    condition = condition or Condition.UNKNOWN

    duplicates = []
    if vendor_serial:
        duplicates = list(check_duplicate_serial(vendor_serial, user=user))
    if duplicates and not duplicate_serial_acknowledged:
        raise DuplicateSerialError(duplicates)

    txn = create_transaction_header(
        movement_type=MovementType.RECEIPT,
        performed_by=user,
        occurred_at=occurred_at,
        destination_location=location,
        project_reference=project_reference,
        final_customer=final_customer,
        notes=notes,
        duplicate_serial_acknowledged=bool(duplicates),
    )

    asset = UnitAsset(
        product=product,
        vendor_serial=vendor_serial,
        status=UnitStatus.IN_STOCK,
        current_location=location,
        project_reference=project_reference,
        final_customer=final_customer,
        supplier=supplier,
        invoice_number=invoice_number,
        arrival_date=occurred_at,
        condition=condition,
        accessories=accessories,
        notes=notes,
        created_by=user,
        updated_by=user,
    )
    asset.full_clean(exclude=["normalized_serial"])
    asset.save()

    InventoryTransactionLine.objects.create(
        transaction=txn,
        line_number=1,
        unit_asset=asset,
        product=product,
        quantity_delta=1,
        from_status=None,
        to_status=UnitStatus.IN_STOCK,
        from_location=None,
        to_location=location,
        brand_snapshot=product.brand.name,
        model_snapshot=product.model,
        sku_snapshot=product.sku,
        type_snapshot=product.product_type.name,
        description_snapshot=product.description,
        serial_snapshot=vendor_serial,
        project_reference_snapshot=project_reference,
        final_customer_snapshot=final_customer,
        supplier_snapshot=supplier,
        invoice_number_snapshot=invoice_number,
        condition_snapshot=condition,
        accessories_snapshot=accessories,
        notes=notes,
    )

    AssetStatusHistory.objects.create(
        unit_asset=asset,
        transaction=txn,
        from_status=None,
        to_status=UnitStatus.IN_STOCK,
        from_location=None,
        to_location=location,
        recorded_by=user,
    )

    if duplicates:
        record_event(
            actor=user,
            event_type=AuditEvent.EventType.DUPLICATE_SERIAL_ACKNOWLEDGED,
            obj=asset,
            summary=f"Acknowledged duplicate serial '{vendor_serial}' when receiving {product}",
            metadata={"matched_unit_asset_ids": [str(a.pk) for a in duplicates]},
        )

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.MOVEMENT_COMPLETED,
        obj=txn,
        summary=f"Received {product} (serial: {vendor_serial or 'none'}) at {location}",
        new_values={"unit_asset_id": str(asset.pk)},
    )
    return txn


def _receive_quantity(
    *,
    user,
    product,
    location,
    occurred_at,
    quantity,
    project_reference,
    final_customer,
    supplier,
    invoice_number,
    notes,
):
    if not quantity or quantity <= 0:
        raise ValidationError("Quantity must be a positive number for quantity-tracked products.")

    txn = create_transaction_header(
        movement_type=MovementType.RECEIPT,
        performed_by=user,
        occurred_at=occurred_at,
        destination_location=location,
        project_reference=project_reference,
        final_customer=final_customer,
        notes=notes,
    )

    balance, _ = StockBalance.objects.select_for_update().get_or_create(
        product=product, location=location
    )
    balance.on_hand_quantity += quantity
    balance.full_clean()
    balance.save()

    InventoryTransactionLine.objects.create(
        transaction=txn,
        line_number=1,
        unit_asset=None,
        product=product,
        quantity_delta=quantity,
        from_location=None,
        to_location=location,
        brand_snapshot=product.brand.name,
        model_snapshot=product.model,
        sku_snapshot=product.sku,
        type_snapshot=product.product_type.name,
        description_snapshot=product.description,
        project_reference_snapshot=project_reference,
        final_customer_snapshot=final_customer,
        supplier_snapshot=supplier,
        invoice_number_snapshot=invoice_number,
        notes=notes,
    )

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.MOVEMENT_COMPLETED,
        obj=txn,
        summary=f"Received {quantity} x {product} at {location}",
        new_values={"quantity": quantity, "balance_id": str(balance.pk)},
    )
    return txn
