"""The only module that writes InventoryTransaction/InventoryTransactionLine/
AssetStatusHistory rows or mutates StockBalance — every movement service is
built on these primitives so there is exactly one code path for each kind of
ledger write (docs/architecture/01-repository-structure.md).

Callers are responsible for their own transition validation (see
apps.inventory.transitions.validate_unit_transition) *before* calling
write_unit_line — this module applies changes, it doesn't decide whether
they're allowed (Administrator corrections/reversals intentionally skip that
check and call straight in here).
"""

from django.core.exceptions import ValidationError
from django.db import connection

from apps.catalog.models import TrackingMethod

from ..models import (
    AssetStatusHistory,
    InventoryTransaction,
    InventoryTransactionLine,
    StockBalance,
    StockPurpose,
    UnitStatus,
)

_CUSTODY_STATUSES = (UnitStatus.ASSIGNED, UnitStatus.DELIVERED)


def next_transaction_number():
    """Backed by a PostgreSQL SEQUENCE (apps/inventory/migrations/0002) —
    monotonic and unique but not gapless, which is fine: doc 02 only requires
    "unique sequential," and gapless numbering would force serializing every
    transaction write.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('inventory_transaction_number_seq')")
        value = cursor.fetchone()[0]
    return f"TXN-{value:06d}"


def create_transaction_header(
    *,
    movement_type,
    performed_by,
    occurred_at,
    source_location=None,
    destination_location=None,
    project_reference="",
    final_customer="",
    customer=None,
    employee_name="",
    is_temporary_assignment=None,
    expected_return_date=None,
    wipe_method="",
    witness_name="",
    recipient_reference="",
    notes="",
    related_transaction=None,
    duplicate_serial_acknowledged=False,
):
    return InventoryTransaction.objects.create(
        transaction_number=next_transaction_number(),
        movement_type=movement_type,
        performed_by=performed_by,
        occurred_at=occurred_at,
        source_location=source_location,
        destination_location=destination_location,
        project_reference=project_reference,
        final_customer=final_customer,
        customer=customer,
        employee_name=employee_name,
        recipient_reference=recipient_reference,
        is_temporary_assignment=is_temporary_assignment,
        expected_return_date=expected_return_date,
        wipe_method=wipe_method,
        witness_name=witness_name,
        notes=notes,
        related_transaction=related_transaction,
        duplicate_serial_acknowledged=duplicate_serial_acknowledged,
    )


def write_unit_line(
    *,
    transaction,
    line_number,
    asset,
    to_status,
    to_location,
    user,
    condition=None,
    accessories=None,
    stock_purpose=None,
    notes="",
):
    """Applies a status/location change to one UnitAsset: writes the
    InventoryTransactionLine snapshot, updates the asset's denormalized
    status/location/condition/accessories/project fields, and writes the
    matching AssetStatusHistory row — all in the caller's transaction.

    `asset` must already be locked (select_for_update()) by the caller when
    concurrent access is possible; this function doesn't lock on its own
    since callers usually lock a whole batch of assets together up front
    (docs/architecture/03-status-and-movement-rules.md's multi-line rules).
    """
    if asset.product.tracking_method != TrackingMethod.UNIT:
        raise ValidationError("Unit ledger lines require a unit-tracked product.")

    from_status = asset.status
    from_location = asset.current_location

    line = InventoryTransactionLine.objects.create(
        transaction=transaction,
        line_number=line_number,
        unit_asset=asset,
        product=asset.product,
        quantity_delta=1,
        stock_purpose_snapshot=stock_purpose or asset.stock_purpose,
        from_status=from_status,
        to_status=to_status,
        from_location=from_location,
        to_location=to_location,
        brand_snapshot=asset.product.brand.name,
        model_snapshot=asset.product.model,
        sku_snapshot=asset.product.sku,
        type_snapshot=asset.product.product_type.name,
        description_snapshot=asset.product.description,
        serial_snapshot=asset.vendor_serial,
        project_reference_snapshot=transaction.project_reference or asset.project_reference,
        final_customer_snapshot=transaction.final_customer or asset.final_customer,
        supplier_snapshot=asset.supplier,
        invoice_number_snapshot=asset.invoice_number,
        condition_snapshot=condition or asset.condition,
        accessories_snapshot=accessories if accessories is not None else asset.accessories,
        notes=notes,
    )

    asset.status = to_status
    asset.current_location = to_location
    if condition is not None:
        asset.condition = condition
    if accessories is not None:
        asset.accessories = accessories
    if stock_purpose is not None:
        asset.stock_purpose = stock_purpose
    if transaction.project_reference:
        asset.project_reference = transaction.project_reference
    if transaction.final_customer:
        asset.final_customer = transaction.final_customer
    if to_location is None and from_location is not None:
        # Physically leaves storage — spec §8's Removal Date, preserved
        # through a later return (doc 02/03).
        asset.last_removal_date = transaction.occurred_at
    if to_status in _CUSTODY_STATUSES:
        # Establishing (or re-establishing, via an Administrator correction)
        # custody — every "Assigned To" display field is read from this
        # transaction, so it becomes the new custody pointer.
        asset.current_custody_transaction = transaction
    elif from_status in _CUSTODY_STATUSES:
        # Leaving Assigned/Delivered (return, or a direct damaged/lost/
        # disposed/correction from that state) — no one currently holds it.
        asset.current_custody_transaction = None
    asset.updated_by = user
    asset.full_clean(exclude=["normalized_serial"])
    asset.save()

    AssetStatusHistory.objects.create(
        unit_asset=asset,
        transaction=transaction,
        from_status=from_status,
        to_status=to_status,
        from_location=from_location,
        to_location=to_location,
        recorded_by=user,
    )
    return line


def write_quantity_line(
    *,
    transaction,
    line_number,
    product,
    quantity_delta,
    from_location=None,
    to_location=None,
    stock_purpose=StockPurpose.INTERNAL,
    project_reference="",
    final_customer="",
    supplier="",
    invoice_number="",
    notes="",
):
    if product.tracking_method != TrackingMethod.QUANTITY:
        raise ValidationError("Quantity ledger lines require a quantity-tracked product.")
    if not quantity_delta:
        raise ValidationError("Quantity ledger lines require a non-zero quantity delta.")
    return InventoryTransactionLine.objects.create(
        transaction=transaction,
        line_number=line_number,
        unit_asset=None,
        product=product,
        stock_purpose_snapshot=stock_purpose,
        quantity_delta=quantity_delta,
        from_location=from_location,
        to_location=to_location,
        brand_snapshot=product.brand.name,
        model_snapshot=product.model,
        sku_snapshot=product.sku,
        type_snapshot=product.product_type.name,
        description_snapshot=product.description,
        project_reference_snapshot=project_reference or transaction.project_reference,
        final_customer_snapshot=final_customer or transaction.final_customer,
        supplier_snapshot=supplier,
        invoice_number_snapshot=invoice_number,
        notes=notes,
    )


def write_reservation_line(
    *, transaction, line_number, reservation, reserved_quantity_delta, notes=""
):
    product = reservation.product
    if product.tracking_method != TrackingMethod.QUANTITY:
        raise ValidationError("Reservation ledger lines require a quantity-tracked product.")
    if not reserved_quantity_delta:
        raise ValidationError("Reservation ledger lines require a non-zero reserved delta.")
    return InventoryTransactionLine.objects.create(
        transaction=transaction,
        line_number=line_number,
        unit_asset=None,
        product=product,
        stock_reservation=reservation,
        stock_purpose_snapshot=reservation.stock_purpose,
        quantity_delta=0,
        reserved_quantity_delta=reserved_quantity_delta,
        from_location=reservation.location,
        to_location=reservation.location,
        brand_snapshot=product.brand.name,
        model_snapshot=product.model,
        sku_snapshot=product.sku,
        type_snapshot=product.product_type.name,
        description_snapshot=product.description,
        project_reference_snapshot=reservation.project_reference,
        final_customer_snapshot=reservation.final_customer,
        notes=notes,
    )


def adjust_balance(
    *, product, location, delta, stock_purpose=StockPurpose.INTERNAL, respect_available=True
):
    """Locks and mutates the (product, location, stock_purpose) StockBalance
    row. `respect_available` (default True) additionally rejects a negative-
    delta change that would dip into reserved quantity — receipts (always
    positive) are unaffected; only Administrator corrections pass False,
    deliberately, to allow a direct on-hand adjustment.
    """
    if product.tracking_method != TrackingMethod.QUANTITY:
        raise ValidationError("Stock balances require a quantity-tracked product.")
    balance, _ = StockBalance.objects.select_for_update().get_or_create(
        product=product, location=location, stock_purpose=stock_purpose
    )

    if respect_available and delta < 0 and (balance.available_quantity + delta) < 0:
        raise ValidationError(
            f"Insufficient available stock: {product} at {location} "
            f"({balance.get_stock_purpose_display()}) has {balance.available_quantity} available."
        )

    new_on_hand = balance.on_hand_quantity + delta
    if new_on_hand < 0:
        raise ValidationError(
            f"Insufficient stock: {product} at {location} "
            f"({balance.get_stock_purpose_display()}) has {balance.on_hand_quantity} on hand."
        )
    balance.on_hand_quantity = new_on_hand
    balance.full_clean()
    balance.save()
    return balance


def adjust_reserved(*, product, location, delta, stock_purpose=StockPurpose.INTERNAL):
    if product.tracking_method != TrackingMethod.QUANTITY:
        raise ValidationError("Stock reservations require a quantity-tracked product.")
    balance = StockBalance.objects.select_for_update().get(
        product=product, location=location, stock_purpose=stock_purpose
    )

    new_reserved = balance.reserved_quantity + delta
    if new_reserved < 0:
        raise ValidationError("Reserved quantity cannot go negative.")
    if new_reserved > balance.on_hand_quantity:
        raise ValidationError(
            f"Cannot reserve {delta} x {product} at {location}: only "
            f"{balance.on_hand_quantity - balance.reserved_quantity} available."
        )
    balance.reserved_quantity = new_reserved
    balance.full_clean()
    balance.save()
    return balance
