from django.core.exceptions import ValidationError
from django.db import connection, transaction

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
    StockPurpose,
    UnitAsset,
    UnitStatus,
)
from .duplicates import check_duplicate_serial, duplicate_serial_count
from .ledger import adjust_balance, create_transaction_header


class DuplicateSerialError(Exception):
    """Raised when a receive_stock() call finds serial matches and the caller
    hasn't set duplicate_serial_acknowledged=True. Carries the matches so the
    view can show them (docs/architecture/05-tracking-and-duplicates.md).
    """

    def __init__(self, matches, by_serial=None):
        self.matches = list(matches)
        # Populated only by receive_stock_bulk(), which can flag more than one
        # serial in a single call — maps each offending serial to its own
        # match list so the review screen can point at the right row.
        self.by_serial = dict(by_serial) if by_serial else {}
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
    stock_purpose=StockPurpose.INTERNAL,
    project_reference="",
    final_customer="",
    supplier="",
    invoice_number="",
    condition=Condition.USED,
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
            stock_purpose=stock_purpose,
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
        stock_purpose=stock_purpose,
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
    stock_purpose=StockPurpose.INTERNAL,
    project_reference,
    final_customer,
    supplier,
    invoice_number,
    condition,
    accessories,
    notes,
    duplicate_serial_acknowledged,
):
    condition = condition or Condition.USED

    duplicates = []
    duplicate_count = 0
    if vendor_serial:
        normalized_serial = " ".join(vendor_serial.split()).upper()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [normalized_serial]
            )
        duplicates = list(check_duplicate_serial(vendor_serial, user=user))
        duplicate_count = duplicate_serial_count(vendor_serial)
    if duplicate_count and not duplicate_serial_acknowledged:
        raise DuplicateSerialError(duplicates)

    txn = create_transaction_header(
        movement_type=MovementType.RECEIPT,
        performed_by=user,
        occurred_at=occurred_at,
        destination_location=location,
        project_reference=project_reference,
        final_customer=final_customer,
        notes=notes,
        duplicate_serial_acknowledged=bool(duplicate_count),
    )

    asset = UnitAsset(
        product=product,
        vendor_serial=vendor_serial,
        status=UnitStatus.IN_STOCK,
        stock_purpose=stock_purpose,
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
        stock_purpose_snapshot=stock_purpose,
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

    if duplicate_count:
        record_event(
            actor=user,
            event_type=AuditEvent.EventType.DUPLICATE_SERIAL_ACKNOWLEDGED,
            obj=asset,
            summary=f"Acknowledged duplicate serial '{vendor_serial}' when receiving {product}",
            metadata={
                "matched_unit_asset_ids": [str(a.pk) for a in duplicates],
                "match_count": duplicate_count,
            },
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
    stock_purpose=StockPurpose.INTERNAL,
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
        product=product, location=location, stock_purpose=stock_purpose
    )
    balance.on_hand_quantity += quantity
    balance.full_clean()
    balance.save()

    InventoryTransactionLine.objects.create(
        transaction=txn,
        line_number=1,
        unit_asset=None,
        product=product,
        stock_purpose_snapshot=stock_purpose,
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


def receive_stock_batch(*, user, product, location, occurred_at, vendor_serials, **shared_fields):
    """Quick Receive (apps.inventory.views.QuickReceiveView) — one serial per
    line pasted/typed into a single form, for the common "we just got a box
    of N identical units" case. Serialized (unit-tracked) products only;
    quantity-tracked products already receive an arbitrary count in one
    receive_stock() call and have no per-unit serial to enumerate.

    Calls receive_stock() once per non-blank line rather than writing one
    InventoryTransaction with many lines — each physical unit's arrival is
    its own receipt event, consistent with how a single manual receive
    works today, and it means a bad row (a typo'd duplicate serial, an
    inactive product caught between rows) doesn't roll back every serial
    that already succeeded: this returns a per-serial outcome list instead
    of raising, so the view can show exactly what happened to each one.
    Never auto-acknowledges a duplicate serial — that confirmation is a
    deliberate human decision (docs/architecture/05-tracking-and-duplicates.md),
    so a duplicate row is reported back, not silently accepted.

    Permission/product-state checks happen once, up front — not per row,
    so a StockManager without access to `location` gets one PermissionDenied
    for the whole submission rather than the same failure N times over.
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    require_location_access(user, location)
    if not product.is_active:
        raise ValidationError("Cannot receive stock for an inactive product.")
    if product.tracking_method != TrackingMethod.UNIT:
        raise ValidationError("Quick Receive is for serialized (unit-tracked) products only.")

    results = []
    seen = set()
    for raw_serial in vendor_serials:
        serial = raw_serial.strip()
        if not serial:
            continue
        normalized = " ".join(serial.split()).upper()
        if normalized in seen:
            results.append({"serial": serial, "status": "skipped_repeat_in_batch"})
            continue
        seen.add(normalized)
        try:
            txn = receive_stock(
                user=user,
                product=product,
                location=location,
                occurred_at=occurred_at,
                vendor_serial=serial,
                **shared_fields,
            )
        except DuplicateSerialError as exc:
            results.append({"serial": serial, "status": "duplicate", "matches": list(exc.matches)})
        except ValidationError as exc:
            results.append({"serial": serial, "status": "error", "detail": "; ".join(exc.messages)})
        else:
            results.append({"serial": serial, "status": "created", "transaction": txn})
    return results


@transaction.atomic
def receive_stock_bulk(
    *,
    user,
    occurred_at,
    default_location,
    lines,
    default_stock_purpose=StockPurpose.INTERNAL,
    supplier="",
    invoice_number="",
    project_reference="",
    final_customer="",
    notes="",
    duplicate_serial_acknowledged=False,
):
    """One atomic multi-line goods receipt covering several products, mixed
    serialized/quantity, in a single InventoryTransaction — unlike
    receive_stock_batch() (one receive_stock() call per serial, explicitly
    not atomic across rows), every line here is validated up front and
    written inside this one @transaction.atomic block, so a bad line
    anywhere rolls back the entire receipt.

    `lines`: a list of dicts, each shaped either
        {"product": Product, "vendor_serials": [str, ...], "location"?: Location,
         "stock_purpose"?: str, "condition"?: str, "accessories"?: str, "notes"?: str}
    for a unit-tracked product, or
        {"product": Product, "quantity": int, "location"?: Location,
         "stock_purpose"?: str, "notes"?: str}
    for a quantity-tracked product. Per-line `location`/`stock_purpose` fall
    back to `default_location`/`default_stock_purpose` when omitted — "apply
    one location/purpose to the batch or override it for individual items".
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    if not lines:
        raise ValidationError("A receipt must contain at least one line.")

    # Pass 1: validate every line and resolve its effective location/purpose
    # before writing anything (multi-line transactions validate all lines
    # first, per docs/architecture/03-status-and-movement-rules.md).
    resolved_lines = []
    seen_serials = set()
    duplicates_by_serial = {}
    for index, raw_line in enumerate(lines, start=1):
        product = raw_line["product"]
        location = raw_line.get("location") or default_location
        stock_purpose = raw_line.get("stock_purpose") or default_stock_purpose
        require_location_access(user, location)
        if not product.is_active:
            raise ValidationError(f"Line {index}: cannot receive stock for an inactive product.")

        if product.tracking_method == TrackingMethod.UNIT:
            serials = [s.strip() for s in raw_line.get("vendor_serials", []) if s.strip()]
            if not serials:
                raise ValidationError(f"Line {index}: at least one serial is required.")
            for serial in serials:
                normalized = " ".join(serial.split()).upper()
                if normalized in seen_serials:
                    raise ValidationError(
                        f"Line {index}: serial '{serial}' is repeated elsewhere in this receipt."
                    )
                seen_serials.add(normalized)
                matches = list(check_duplicate_serial(serial, user=user))
                if matches and not duplicate_serial_acknowledged:
                    duplicates_by_serial[serial] = matches
            resolved_lines.append(
                {
                    "kind": "unit",
                    "product": product,
                    "location": location,
                    "stock_purpose": stock_purpose,
                    "serials": serials,
                    "condition": raw_line.get("condition") or Condition.USED,
                    "accessories": raw_line.get("accessories", ""),
                    "notes": raw_line.get("notes", ""),
                }
            )
        else:
            quantity = raw_line.get("quantity")
            if not quantity or quantity <= 0:
                raise ValidationError(f"Line {index}: quantity must be a positive number.")
            resolved_lines.append(
                {
                    "kind": "quantity",
                    "product": product,
                    "location": location,
                    "stock_purpose": stock_purpose,
                    "quantity": quantity,
                    "notes": raw_line.get("notes", ""),
                }
            )

    if duplicates_by_serial:
        all_matches = [m for matches in duplicates_by_serial.values() for m in matches]
        raise DuplicateSerialError(all_matches, by_serial=duplicates_by_serial)

    # Pass 2: write everything — one header, N lines.
    txn = create_transaction_header(
        movement_type=MovementType.RECEIPT,
        performed_by=user,
        occurred_at=occurred_at,
        destination_location=default_location,
        project_reference=project_reference,
        final_customer=final_customer,
        notes=notes,
        duplicate_serial_acknowledged=bool(seen_serials) and duplicate_serial_acknowledged,
    )

    line_number = 0
    created_asset_ids = []
    touched_balance_ids = set()
    for resolved in resolved_lines:
        product = resolved["product"]
        location = resolved["location"]
        stock_purpose = resolved["stock_purpose"]
        if resolved["kind"] == "unit":
            for serial in resolved["serials"]:
                line_number += 1
                asset = UnitAsset(
                    product=product,
                    vendor_serial=serial,
                    status=UnitStatus.IN_STOCK,
                    stock_purpose=stock_purpose,
                    current_location=location,
                    project_reference=project_reference,
                    final_customer=final_customer,
                    supplier=supplier,
                    invoice_number=invoice_number,
                    arrival_date=occurred_at,
                    condition=resolved["condition"],
                    accessories=resolved["accessories"],
                    notes=resolved["notes"],
                    created_by=user,
                    updated_by=user,
                )
                asset.full_clean(exclude=["normalized_serial"])
                asset.save()
                InventoryTransactionLine.objects.create(
                    transaction=txn,
                    line_number=line_number,
                    unit_asset=asset,
                    product=product,
                    stock_purpose_snapshot=stock_purpose,
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
                    serial_snapshot=serial,
                    project_reference_snapshot=project_reference,
                    final_customer_snapshot=final_customer,
                    supplier_snapshot=supplier,
                    invoice_number_snapshot=invoice_number,
                    condition_snapshot=resolved["condition"],
                    accessories_snapshot=resolved["accessories"],
                    notes=resolved["notes"],
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
                created_asset_ids.append(str(asset.pk))
        else:
            line_number += 1
            balance = adjust_balance(
                product=product,
                location=location,
                delta=resolved["quantity"],
                stock_purpose=stock_purpose,
            )
            InventoryTransactionLine.objects.create(
                transaction=txn,
                line_number=line_number,
                unit_asset=None,
                product=product,
                stock_purpose_snapshot=stock_purpose,
                quantity_delta=resolved["quantity"],
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
                notes=resolved["notes"],
            )
            touched_balance_ids.add(str(balance.pk))

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.MOVEMENT_COMPLETED,
        obj=txn,
        summary=f"Received {len(resolved_lines)} line(s) into stock ({txn.transaction_number})",
        new_values={
            "unit_asset_ids": created_asset_ids,
            "balance_ids": sorted(touched_balance_ids),
        },
    )
    return txn
