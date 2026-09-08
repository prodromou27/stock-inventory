import csv
import datetime
import io

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.catalog.models import Brand, ItemCategory, Product, TrackingMethod
from apps.catalog.services import check_duplicate_products, resolve_or_create_product
from apps.core.authorization import ADMINISTRATOR, require_role
from apps.core.spreadsheets import spreadsheet_safe_row
from apps.inventory.models import StockPurpose
from apps.inventory.services.assignments import assign_to_employee, deliver_to_customer
from apps.inventory.services.internal_use import change_internal_use
from apps.inventory.services.receipts import receive_stock

from . import parsing
from .location_resolution import resolve_location
from .models import ImportBatch, ImportBatchStatus, ImportRow, ImportRowOutcome
from .normalization import (
    normalize_quantity,
    normalize_text,
    normalize_vendor_serial,
    parse_legacy_date,
)

EXECUTE_BATCH_SIZE = 500

STATUS_CHOICES = [
    ("in_stock", "In Stock"),
    ("assigned", "Assigned"),
    ("delivered", "Delivered"),
    ("in_use", "In Use"),
]


def validate_lifecycle(data):
    status = data.get("import_status")
    if status not in dict(STATUS_CHOICES):
        raise ValidationError(
            "Choose In Stock, Assigned, Delivered, or In Use in the status review. "
            "Blank locations never imply In Stock."
        )
    if status == "in_stock":
        return
    if not data.get("movement_date"):
        raise ValidationError("An issued/installed item requires a valid Movement Date.")
    if data.get("used_default_arrival_date"):
        raise ValidationError(
            "Issued/installed stock requires an explicit Arrival Date. "
            "Correct it in the status review."
        )
    movement_date = datetime.date.fromisoformat(data["movement_date"])
    if data.get("arrival_date") and movement_date < datetime.date.fromisoformat(
        data["arrival_date"]
    ):
        raise ValidationError(
            "Movement Date cannot precede Arrival Date. Correct the source dates before import."
        )
    if status == "assigned" and not data.get("employee"):
        raise ValidationError("Assigned stock requires an Employee.")
    if status == "delivered" and (
        not data.get("final_customer") or not data.get("project_reference")
    ):
        raise ValidationError("Delivered stock requires Final Customer and Project Reference.")
    if status == "in_use":
        if (
            data.get("tracking_method") != "unit"
            or data.get("stock_purpose") != StockPurpose.INTERNAL
        ):
            raise ValidationError("In Use requires unit tracking and Internal stock purpose.")
        if not data.get("installation_notes"):
            raise ValidationError("In Use requires Installation Notes.")


def apply_import_movement(*, user, data, receipt, product, location):
    status = data["import_status"]
    if status == "in_stock":
        return receipt
    line = receipt.lines.get()
    params = dict(
        user=user,
        occurred_at=datetime.date.fromisoformat(data["movement_date"]),
        notes=data.get("notes", ""),
    )
    if line.unit_asset_id:
        params["unit_asset_ids"] = [line.unit_asset_id]
    else:
        params["quantity_lines"] = [
            {
                "product": product,
                "location": location,
                "quantity": line.quantity_delta,
                "stock_purpose": data.get("stock_purpose", "internal"),
            }
        ]
    if status == "in_use":
        params["notes"] = data["installation_notes"]
        return change_internal_use(**params)
    params["project_reference"] = data.get("project_reference", "")
    if status == "assigned":
        return assign_to_employee(**params, employee_name=data["employee"])
    return deliver_to_customer(**params, final_customer=data["final_customer"])


# A worker that crashes/is killed mid-execute_batch() leaves a batch stuck
# at EXECUTING forever (the status is committed before the row loop, which
# then runs outside any transaction) — with no recovery path short of a
# manual DB edit. A batch that hasn't advanced its own updated_at (touched
# at every chunk boundary below, not just on entry) for longer than this is
# treated as failed rather than genuinely still running.
STALE_EXECUTION_TIMEOUT = datetime.timedelta(minutes=15)


def is_stale_execution(batch):
    return (
        batch.status == ImportBatchStatus.EXECUTING
        and timezone.now() - batch.updated_at > STALE_EXECUTION_TIMEOUT
    )


# --- Upload + staging --------------------------------------------------


@transaction.atomic
def create_batch_from_upload(
    *, uploaded_file, user, default_location=None, default_stock_purpose=StockPurpose.INTERNAL
):
    """Parses the file, creates the ImportBatch, and stages every row
    (doc 07 steps 1-4 collapsed into one pass — each row is independent and
    validation is cheap, so there's no benefit to a separate DB round trip
    per stage for a first version of this pipeline).

    `default_location`/`default_stock_purpose` are the batch-wide fallbacks
    a row uses only when its own LOCATION/Stock Purpose columns don't
    resolve — per-row values always win when present (_stage_row()).
    """
    require_role(user, ADMINISTRATOR)

    if uploaded_file.size > parsing.MAX_IMPORT_SIZE_BYTES:
        raise ValidationError("Import files must be 25 MB or smaller.")
    file_bytes = uploaded_file.read()
    checksum = parsing.compute_checksum(file_bytes)
    rows = parsing.parse_rows(filename=uploaded_file.name, file_bytes=file_bytes)

    is_repeat_upload = ImportBatch.objects.filter(
        file_checksum=checksum, status=ImportBatchStatus.COMPLETED
    ).exists()

    batch = ImportBatch(
        source_filename=uploaded_file.name,
        file_checksum=checksum,
        uploaded_by=user,
        status=ImportBatchStatus.PREVIEWED,
        default_location=default_location,
        default_stock_purpose=default_stock_purpose,
    )
    batch.file.save(uploaded_file.name, ContentFile(file_bytes), save=False)
    batch.full_clean(exclude=["file"])
    batch.save()

    warning_count = 0
    failed_count = 0
    import_rows = []
    for row_number, raw in rows:
        normalized, outcome, detail = _stage_row(
            raw, default_location=default_location, default_stock_purpose=default_stock_purpose
        )
        if outcome == ImportRowOutcome.WARNING:
            warning_count += 1
        elif outcome == ImportRowOutcome.FAILED:
            failed_count += 1
        import_rows.append(
            ImportRow(
                batch=batch,
                row_number=row_number,
                raw_data=_json_safe(raw),
                normalized_data=normalized,
                outcome=outcome,
                outcome_detail=detail,
            )
        )
    ImportRow.objects.bulk_create(import_rows)

    batch.warning_count = warning_count
    batch.failed_count = failed_count
    batch.save(update_fields=["warning_count", "failed_count"])

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_CREATED,
        obj=batch,
        summary=f"Uploaded import batch '{batch.source_filename}' ({len(import_rows)} rows)",
        metadata={
            "repeat_of_completed_checksum": is_repeat_upload,
            "row_count": len(import_rows),
        },
    )
    return batch, is_repeat_upload


def _json_safe(raw):
    return {
        key: (value if not hasattr(value, "isoformat") else value.isoformat())
        for key, value in raw.items()
    }


def _stage_row(raw, *, default_location=None, default_stock_purpose=StockPurpose.INTERNAL):
    issues = []
    warnings = []

    brand_name = normalize_text(raw.get("BRAND"))
    model = normalize_text(raw.get("MODEL/Part No./SKU"))
    product_type_name = normalize_text(raw.get("TYPE/DESCRIPTION"))
    if not brand_name:
        issues.append("Brand is required.")
    if not model:
        issues.append("Model is required.")
    if not product_type_name:
        issues.append("Type is required.")

    serial_text, normalized_serial = normalize_vendor_serial(raw.get("S/N"))
    quantity, quantity_valid = normalize_quantity(raw.get("QTY"))
    if not quantity_valid:
        issues.append(f"Quantity '{raw.get('QTY')}' is not a valid non-negative whole number.")

    tracking_text = normalize_text(raw.get("Tracking Method")).lower()
    tracking_method = TrackingMethod.UNIT if serial_text else TrackingMethod.QUANTITY
    if tracking_text in TrackingMethod.values:
        tracking_method = TrackingMethod(tracking_text)
    elif tracking_text:
        issues.append("Tracking Method must be unit or quantity.")
    if tracking_method == TrackingMethod.QUANTITY and serial_text:
        issues.append("A quantity-tracked row cannot contain a serial number.")
    if tracking_method == TrackingMethod.UNIT and quantity and quantity > 1:
        issues.append(
            "Unit-tracked stock requires one row per item (QTY 1), even without a serial."
        )
    if tracking_method == TrackingMethod.QUANTITY and (quantity is None or quantity <= 0):
        issues.append("Quantity-tracked rows (no serial number given) need a positive QTY.")

    conflict = _tracking_method_conflict(brand_name, model, tracking_method)
    if conflict:
        issues.append(conflict)

    location_text = normalize_text(raw.get("LOCATION"))
    sub_location_text = normalize_text(raw.get("2nd floor Location"))
    resolved_location, location_detail = resolve_location(location_text, sub_location_text)
    used_batch_default_location = False
    if resolved_location is None and default_location is not None:
        resolved_location = default_location
        used_batch_default_location = True
    elif resolved_location is None and not raw.get("Source Room"):
        warnings.append(location_detail or f"Unknown location '{location_text}'.")

    stock_purpose_text = normalize_text(raw.get("Stock Purpose")).lower()
    if stock_purpose_text in StockPurpose.values:
        stock_purpose = stock_purpose_text
    elif stock_purpose_text:
        warnings.append(
            f"Stock Purpose '{raw.get('Stock Purpose')}' is not recognized; "
            f"using the batch default ({StockPurpose(default_stock_purpose).label})."
        )
        stock_purpose = default_stock_purpose
    else:
        stock_purpose = default_stock_purpose

    arrival_date, arrival_valid = parse_legacy_date(raw.get("Arrival Date"))
    if not arrival_valid:
        warnings.append(
            f"Arrival Date '{raw.get('Arrival Date')}' could not be parsed; "
            "today's date will be used."
        )
    # Resolved and frozen here, at staging time — not deferred to execute
    # time — so the batch-detail preview shows the exact date that will be
    # written, even if the batch sits for days before being executed.
    used_default_arrival_date = arrival_date is None
    if arrival_date is None:
        arrival_date = timezone.localdate()

    # apps.inventory.services.duplicates.check_duplicate_serial is scope-aware
    # and requires a real user; staging happens before we know which user will
    # execute the batch, so an unscoped match against all unit assets is used
    # here instead (execution re-checks nothing further — the batch-wide
    # match is treated as sufficient grounds for the warning).
    duplicate_serial_ids = []
    if serial_text:
        from apps.inventory.models import UnitAsset

        duplicate_serial_ids = list(
            UnitAsset.objects.filter(normalized_serial=normalized_serial).values_list(
                "id", flat=True
            )
        )
        if duplicate_serial_ids:
            warnings.append(
                f"Serial '{serial_text}' matches {len(duplicate_serial_ids)} "
                "existing unit asset(s)."
            )

    duplicate_product_ids = []
    existing_brand = Brand.objects.filter(name__iexact=brand_name).first() if brand_name else None
    if existing_brand and model:
        duplicate_product_ids = list(
            check_duplicate_products(brand=existing_brand, model=model).values_list("id", flat=True)
        )
        if duplicate_product_ids:
            warnings.append(
                f"'{brand_name} {model}' matches {len(duplicate_product_ids)} existing product(s)."
            )

    notes = _build_legacy_notes(raw)

    normalized = {
        "brand_name": brand_name,
        "model": model,
        "product_type_name": product_type_name,
        "vendor_serial": serial_text,
        "normalized_serial": normalized_serial,
        "tracking_method": tracking_method,
        "quantity": quantity,
        "location_text": location_text,
        "source_room_text": normalize_text(raw.get("Source Room")),
        "sub_location_text": sub_location_text,
        "resolved_location_id": str(resolved_location.pk) if resolved_location else None,
        "used_batch_default_location": used_batch_default_location,
        "location_override_id": None,
        "stock_purpose": stock_purpose,
        "project_reference": normalize_text(raw.get("Project Ref. #")),
        "final_customer": normalize_text(raw.get("FINAL CUSTOMER")),
        "arrival_date": arrival_date.isoformat(),
        "used_default_arrival_date": used_default_arrival_date,
        "notes": notes,
        "duplicate_serial_ids": [str(pk) for pk in duplicate_serial_ids],
        "duplicate_product_ids": [str(pk) for pk in duplicate_product_ids],
    }

    explicit_status = normalize_text(
        raw.get("Status") or raw.get("PRODUCT DELIVERY / PRODUCT REMOVAL")
    )
    status = explicit_status.lower().replace(" ", "_")
    if (
        not status
        and (location_text or sub_location_text)
        and not any(raw.get(key) for key in ("Delivery Date", "Removal Date", "Return Date"))
    ):
        status = "in_stock"
    movement_raw = raw.get("Movement Date") or raw.get("Delivery Date") or raw.get("Removal Date")
    movement_date, movement_valid = parse_legacy_date(movement_raw)
    normalized.update(
        import_status=status,
        movement_date=movement_date.isoformat() if movement_valid and movement_date else None,
        employee=normalize_text(raw.get("Employee")),
        installation_notes=normalize_text(raw.get("Installation Notes")),
    )
    if raw.get("Source Room") and status != "in_stock":
        source, detail = resolve_location(normalize_text(raw["Source Room"]), "")
        normalized["resolved_location_id"] = str(source.pk) if source else None
        normalized["used_batch_default_location"] = False
        if not source:
            warnings.append(detail or "Source Room could not be resolved.")
    try:
        validate_lifecycle(normalized)
    except ValidationError as exc:
        warnings.extend(exc.messages)
    if not normalized["resolved_location_id"]:
        # Appended even when other warnings already exist — this is a
        # different, actionable problem (nothing else here tells the
        # operator what to actually go do about a missing location), not
        # a duplicate of e.g. a lifecycle-validation warning.
        warnings.append("Select a receipt/source room in the preview.")

    if issues:
        return normalized, ImportRowOutcome.FAILED, " ".join(issues)
    if warnings:
        return normalized, ImportRowOutcome.WARNING, " ".join(dict.fromkeys(warnings))
    return normalized, ImportRowOutcome.PENDING, ""


def _tracking_method_conflict(brand_name, model, tracking_method):
    if not brand_name or not model:
        return ""
    brand = Brand.objects.filter(name__iexact=brand_name).first()
    if not brand:
        return ""
    product = Product.objects.filter(brand=brand, normalized_model=model.lower()).first()
    if product and product.tracking_method != tracking_method:
        existing_label = product.get_tracking_method_display()
        return (
            f"'{brand_name} {model}' already exists as {existing_label}-tracked, "
            f"but this row implies {tracking_method.label}-tracked. Resolve manually."
        )
    return ""


def _build_legacy_notes(raw):
    parts = []
    comments = normalize_text(raw.get("COMMENTS/#No"))
    if comments:
        parts.append(f"Comments: {comments}")
    delivery_removal = normalize_text(raw.get("PRODUCT DELIVERY / PRODUCT REMOVAL"))
    if delivery_removal:
        parts.append(f"Legacy delivery/removal value: {delivery_removal}")
    for label, column in (
        ("Delivery Date", "Delivery Date"),
        ("Return Date", "Return Date"),
        ("Removal Date", "Removal Date"),
    ):
        value = raw.get(column)
        if value not in (None, ""):
            parsed, valid = parse_legacy_date(value)
            parts.append(f"{label}: {parsed.isoformat() if valid and parsed else value}")
    registrar = normalize_text(raw.get("Registrar"))
    if registrar:
        parts.append(f"Legacy registrar: {registrar}")
    if not parts:
        return ""
    return "Legacy import: " + "; ".join(parts)


# --- Row-level override -------------------------------------------------


@transaction.atomic
def review_row_lifecycle(*, row, user, location, **values):
    from apps.locations.scoping import require_location_access, require_room_or_below

    require_role(user, ADMINISTRATOR)
    batch = ImportBatch.objects.select_for_update().get(pk=row.batch_id)
    _require_editable_batch(batch)
    row = ImportRow.objects.select_for_update().get(pk=row.pk, batch=batch)
    # A FAILED row is reachable here too, not just pending/warning — this
    # is the Administrator's one explicit path back to PENDING for a row
    # execute_batch() itself will never retry on its own (it deliberately
    # skips already-failed rows so an automatic re-run stays idempotent —
    # see execute_batch()'s docstring). A FAILED row never had a
    # successful service call, so resetting and re-running it can't
    # double-apply anything. Without this, a row that failed for a
    # transient reason (a product briefly inactive, a serial that was a
    # duplicate only until the conflicting asset was fixed) was
    # permanently stuck — the batch could never be completed.
    if row.outcome not in (
        ImportRowOutcome.PENDING,
        ImportRowOutcome.WARNING,
        ImportRowOutcome.FAILED,
    ):
        raise ValidationError(
            "Only pending, warning, or failed rows can be reviewed. "
            "Imported/skipped rows are already final."
        )
    require_location_access(user, location)
    require_room_or_below(location)
    if not location.is_active:
        raise ValidationError("Select an active source room.")
    old = dict(row.normalized_data)
    data = dict(old)
    for key in (
        "import_status",
        "arrival_date",
        "movement_date",
        "employee",
        "final_customer",
        "project_reference",
        "installation_notes",
    ):
        # Only ever touches a key the caller actually passed — RowLifecycleForm
        # happens to declare all seven today, but a caller that omits one
        # (a future partial-update action, a management command, a test)
        # must leave that staged value alone, not silently null it out.
        if key in values:
            value = values[key]
            data[key] = value.isoformat() if hasattr(value, "isoformat") else value
    data["location_override_id"] = str(location.pk)
    data["reviewed_location_label"] = str(location)
    data["used_default_arrival_date"] = False
    validate_lifecycle(data)
    row.normalized_data = data
    row.outcome_detail = "Status and source room reviewed."
    row.outcome = ImportRowOutcome.PENDING
    if data.get("duplicate_serial_ids") and not row.duplicate_serial_acknowledged:
        row.outcome = ImportRowOutcome.WARNING
        row.outcome_detail += " Duplicate serial acknowledgement is still required."
    row.save(update_fields=["normalized_data", "outcome_detail", "outcome"])
    batch.warning_count = batch.rows.filter(outcome=ImportRowOutcome.WARNING).count()
    batch.save(update_fields=["warning_count"])
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=row,
        summary=f"Reviewed lifecycle for import row {row.row_number}",
        old_values=old,
        new_values=data,
    )
    return row


def _require_editable_batch(batch):
    if batch.status not in (ImportBatchStatus.PREVIEWED, ImportBatchStatus.PARTIALLY_COMPLETED):
        raise ValidationError(
            "This batch is fully completed (or not yet staged) and its rows can no longer "
            "be edited."
        )


@transaction.atomic
def set_row_location_override(*, row, location, user):
    """Records a manual location choice for a row whose LOCATION/'2nd floor
    Location' text didn't resolve automatically. The row's `outcome` is left
    as-is (still `warning` if other issues remain) — execute_batch() already
    attempts every pending/warning row and checks for an override before
    falling back to the auto-resolved location, so recording the override
    here is sufficient for the row to execute successfully; no separate
    re-validation pass is needed.
    """
    require_role(user, ADMINISTRATOR)
    batch = ImportBatch.objects.select_for_update().get(pk=row.batch_id)
    _require_editable_batch(batch)
    row = ImportRow.objects.select_for_update().get(pk=row.pk)
    if row.outcome in (ImportRowOutcome.IMPORTED, ImportRowOutcome.SKIPPED):
        raise ValidationError("Imported or skipped rows cannot be edited.")
    from apps.locations.scoping import require_location_access, require_room_or_below

    require_location_access(user, location)
    require_room_or_below(location)
    if not location.is_active:
        raise ValidationError("Select an active receipt/source room.")

    old_location_id = row.normalized_data.get("location_override_id")
    normalized = dict(row.normalized_data)
    normalized["location_override_id"] = str(location.pk)
    row.normalized_data = normalized
    row.outcome_detail = f"{row.outcome_detail} (location manually set to {location})".strip()
    row.save(update_fields=["normalized_data", "outcome_detail"])
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=row,
        summary=f"Changed location override for import row {row.row_number}",
        old_values={"location_override_id": old_location_id},
        new_values={"location_override_id": str(location.pk)},
        metadata={"batch_id": str(row.batch_id), "row_number": row.row_number},
    )
    return row


@transaction.atomic
def skip_row(*, row, user):
    require_role(user, ADMINISTRATOR)
    batch = ImportBatch.objects.select_for_update().get(pk=row.batch_id)
    _require_editable_batch(batch)
    row = ImportRow.objects.select_for_update().get(pk=row.pk)
    if row.outcome == ImportRowOutcome.IMPORTED:
        raise ValidationError("Already imported rows cannot be skipped.")
    if row.outcome == ImportRowOutcome.SKIPPED:
        return row
    old_outcome = row.outcome
    row.outcome = ImportRowOutcome.SKIPPED
    row.outcome_detail = "Skipped by user during preview."
    row.save(update_fields=["outcome", "outcome_detail"])
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=row,
        summary=f"Skipped import row {row.row_number}",
        old_values={"outcome": old_outcome},
        new_values={"outcome": ImportRowOutcome.SKIPPED},
        metadata={"batch_id": str(row.batch_id), "row_number": row.row_number},
    )
    return row


@transaction.atomic
def acknowledge_row_duplicate_serial(*, row, user):
    require_role(user, ADMINISTRATOR)
    batch = ImportBatch.objects.select_for_update().get(pk=row.batch_id)
    _require_editable_batch(batch)
    row = ImportRow.objects.select_for_update().get(pk=row.pk)
    if row.outcome in (ImportRowOutcome.IMPORTED, ImportRowOutcome.SKIPPED):
        raise ValidationError("Imported or skipped rows cannot be edited.")
    if not row.normalized_data.get("duplicate_serial_ids"):
        raise ValidationError("This row has no duplicate serial warning.")
    row.duplicate_serial_acknowledged = True
    row.duplicate_serial_acknowledged_by = user
    row.duplicate_serial_acknowledged_at = timezone.now()
    row.save(
        update_fields=[
            "duplicate_serial_acknowledged",
            "duplicate_serial_acknowledged_by",
            "duplicate_serial_acknowledged_at",
        ]
    )
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.DUPLICATE_SERIAL_ACKNOWLEDGED,
        obj=row,
        summary=f"Acknowledged duplicate serial warning for import row {row.row_number}",
        metadata={"batch_id": str(row.batch_id), "row_number": row.row_number},
    )
    return row


# --- Execution ------------------------------------------------------------


def execute_batch(*, batch, user):
    """Runs every not-yet-imported row through receive_stock(), in bounded
    transactions of EXECUTE_BATCH_SIZE rows (doc 07 step 6). Re-running on a
    batch that's already partially executed only touches rows that are still
    pending/warning — imported/skipped/failed rows are left untouched, which
    is what makes a retry idempotent (spec §13, acceptance criterion §21.14).
    """
    require_role(user, ADMINISTRATOR)

    with transaction.atomic():
        batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
        if batch.status == ImportBatchStatus.COMPLETED:
            return batch
        if batch.status == ImportBatchStatus.EXECUTING:
            if not is_stale_execution(batch):
                raise ValidationError("This import batch is already executing.")
            # No progress in over STALE_EXECUTION_TIMEOUT — the worker that
            # was running this almost certainly crashed or was killed.
            # Recorded as FAILED (an already-existing status the daily
            # digest already watches for) before falling through to
            # re-execute, rather than leaving it stuck forever.
            batch.status = ImportBatchStatus.FAILED
            batch.save(update_fields=["status", "updated_at"])
        elif batch.status not in (
            ImportBatchStatus.PREVIEWED,
            ImportBatchStatus.PARTIALLY_COMPLETED,
        ):
            raise ValidationError("This import batch cannot be executed in its current state.")
        batch.status = ImportBatchStatus.EXECUTING
        batch.save(update_fields=["status", "updated_at"])

    rows = list(
        batch.rows.filter(
            outcome__in=[ImportRowOutcome.PENDING, ImportRowOutcome.WARNING]
        ).order_by("row_number")
    )
    for start in range(0, len(rows), EXECUTE_BATCH_SIZE):
        _execute_row_chunk(rows[start : start + EXECUTE_BATCH_SIZE], user=user)
        # Touched after every chunk (not just on entry) so a large,
        # genuinely-still-running import keeps refreshing its own
        # staleness clock instead of looking crashed to a concurrent check.
        batch.save(update_fields=["updated_at"])

    counts = {
        "imported": batch.rows.filter(outcome=ImportRowOutcome.IMPORTED).count(),
        "skipped": batch.rows.filter(outcome=ImportRowOutcome.SKIPPED).count(),
        "warning": batch.rows.filter(outcome=ImportRowOutcome.WARNING).count(),
        "failed": batch.rows.filter(outcome=ImportRowOutcome.FAILED).count(),
    }
    batch.imported_count = counts["imported"]
    batch.skipped_count = counts["skipped"]
    batch.warning_count = counts["warning"]
    batch.failed_count = counts["failed"]
    batch.executed_by = user
    batch.executed_at = timezone.now()
    batch.status = (
        ImportBatchStatus.COMPLETED
        if counts["warning"] == 0 and counts["failed"] == 0
        else ImportBatchStatus.PARTIALLY_COMPLETED
    )
    batch.save(
        update_fields=[
            "imported_count",
            "skipped_count",
            "warning_count",
            "failed_count",
            "executed_by",
            "executed_at",
            "status",
        ]
    )

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.IMPORT_EXECUTED,
        obj=batch,
        summary=f"Executed import batch '{batch.source_filename}'",
        new_values=counts,
    )
    return batch


@transaction.atomic
def _execute_row_chunk(rows, *, user):
    for row in rows:
        _execute_row(row, user=user)


def _execute_row(row, *, user):
    data = row.normalized_data
    if "import_status" not in data:
        fresh, _, _ = _stage_row(
            row.raw_data,
            default_location=row.batch.default_location,
            default_stock_purpose=row.batch.default_stock_purpose,
        )
        for key in ("import_status", "movement_date", "employee", "installation_notes"):
            data[key] = fresh.get(key)
        row.normalized_data = data
        row.save(update_fields=["normalized_data"])
    try:
        validate_lifecycle(data)
    except ValidationError as exc:
        row.outcome = ImportRowOutcome.WARNING
        row.outcome_detail = " ".join(exc.messages)
        row.save(update_fields=["outcome", "outcome_detail"])
        return
    if data.get("duplicate_serial_ids") and not row.duplicate_serial_acknowledged:
        # Appended, not overwritten — row.outcome_detail already names which
        # serial matched and how many existing assets it conflicts with
        # (set at staging time, _stage_row()); losing that here would leave
        # the Administrator with no way to tell what to check before
        # deciding whether to acknowledge.
        row.outcome_detail = (
            f"{row.outcome_detail} Not executed: duplicate serial acknowledgement is required."
        ).strip()
        row.save(update_fields=["outcome_detail"])
        return
    location_id = data.get("location_override_id") or data.get("resolved_location_id")
    if not location_id:
        row.outcome = ImportRowOutcome.WARNING
        row.outcome_detail = (
            f"{row.outcome_detail} Not executed: no resolved or overridden location.".strip()
        )
        row.save(update_fields=["outcome", "outcome_detail"])
        return

    try:
        txn = _import_inventory_row(
            data=data,
            location_id=location_id,
            user=user,
            duplicate_serial_acknowledged=row.duplicate_serial_acknowledged,
        )
    except ValidationError as exc:
        row.outcome = ImportRowOutcome.FAILED
        row.outcome_detail = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        row.save(update_fields=["outcome", "outcome_detail"])
        return
    except (
        Exception
    ) as exc:  # noqa: BLE001 - any service-layer rejection becomes a row failure, not a batch crash
        row.outcome = ImportRowOutcome.FAILED
        row.outcome_detail = str(exc)
        row.save(update_fields=["outcome", "outcome_detail"])
        return

    row.outcome = ImportRowOutcome.IMPORTED
    row.outcome_detail = f"Imported as transaction {txn.transaction_number}."
    row.created_transaction = txn
    line = txn.lines.first()  # receive_stock() always writes exactly one line
    if line and line.unit_asset_id:
        row.created_unit_asset = line.unit_asset
    row.save(
        update_fields=["outcome", "outcome_detail", "created_transaction", "created_unit_asset"]
    )


@transaction.atomic
def _import_inventory_row(*, data, location_id, user, duplicate_serial_acknowledged):
    from apps.locations.scoping import (
        accessible_locations,
        require_location_access,
        require_room_or_below,
    )

    location = accessible_locations(user).get(pk=location_id)
    require_location_access(user, location)
    require_room_or_below(location)
    if not location.is_active:
        raise ValidationError("Select an active receipt/source room.")
    validate_lifecycle(data)
    product = _get_or_create_import_product(
        user=user,
        brand_name=data["brand_name"],
        model=data["model"],
        product_type_name=data["product_type_name"],
        tracking_method=data["tracking_method"],
    )
    receipt = receive_stock(
        user=user,
        product=product,
        location=location,
        occurred_at=datetime.date.fromisoformat(data["arrival_date"]),
        vendor_serial=data["vendor_serial"],
        quantity=data["quantity"]
        or (1 if data["tracking_method"] == TrackingMethod.UNIT else None),
        stock_purpose=data.get("stock_purpose", StockPurpose.INTERNAL),
        project_reference=data["project_reference"],
        final_customer=data["final_customer"],
        notes=data["notes"],
        duplicate_serial_acknowledged=duplicate_serial_acknowledged,
    )
    return apply_import_movement(
        user=user, data=data, receipt=receipt, product=product, location=location
    )


#  Import rows have no explicit Category column (neither request that asked
#  for one), so the same signal that already infers tracking_method (whether
#  the row has a serial) picks the corresponding default category — exactly
#  the same default the catalog.0005 data migration used to classify
#  pre-existing products. Reusing/creating a product that already carries a
#  more specific category (Reusable Accessory, Component, Consumable) still
#  works: resolve_or_create_product() only compares tracking_method on an
#  exact match, never overwrites an existing product's own category.
_IMPORT_DEFAULT_CATEGORY = {
    TrackingMethod.UNIT: ItemCategory.SERIALIZED_ASSET,
    TrackingMethod.QUANTITY: ItemCategory.QUANTITY_STOCK,
}


def _get_or_create_import_product(*, user, brand_name, model, product_type_name, tracking_method):
    """Thin wrapper over the shared apps.catalog.services.resolve_or_create_product()
    — every row in a batch that shares the same brand/model must resolve to
    the *same* Product, not a fresh duplicate per row, and Add Stock's manual
    entry point needs the identical reuse-or-create logic, so both go
    through the one function rather than two independently-maintained copies
    of "what counts as the same product."  duplicate_acknowledged=True
    always: an import row that isn't an exact reuse creates a new product
    outright rather than pausing for interactive confirmation (unlike Add
    Stock's manual flow, an import batch has no per-row human in the loop at
    execute time — the preview step is where that judgment call already
    happened, per apps.imports.services._stage_row()'s duplicate-product
    warning).
    """
    return resolve_or_create_product(
        user=user,
        brand_name=brand_name,
        model=model,
        product_type_name=product_type_name,
        category=_IMPORT_DEFAULT_CATEGORY[tracking_method],
        duplicate_acknowledged=True,
    )


# --- Downloads --------------------------------------------------------


_TEMPLATE_SAMPLE_ROWS = [
    [
        "Cisco",
        "C881",
        "Router",
        "SFCZ2413C362",
        "1",
        "Basement 1",
        "7",
        "Q8832",
        "ZORBAS",
        "",
        "",
        "2026-01-15",
        "",
        "",
        "",
        "",
        "Internal",
        "In Stock",
        "",
        "",
        "",
        "",
        "unit",
    ],
    [
        "Generic",
        "Patch Cable 1m",
        "Cable",
        "",
        "50",
        "Basement 1",
        "",
        "",
        "",
        "",
        "",
        "2026-01-15",
        "",
        "",
        "",
        "",
        "Customer",
        "In Stock",
        "",
        "",
        "",
        "",
        "quantity",
    ],
]

for _status, _employee, _customer, _project, _installation in (
    ("Assigned", "Example Employee", "", "", ""),
    ("Delivered", "", "Example Customer", "PROJECT-001", ""),
    ("In Use", "", "", "", "Installed in server room X"),
):
    _sample = dict(zip(parsing.COLUMNS, _TEMPLATE_SAMPLE_ROWS[0], strict=True))
    _sample.update(
        {
            "Status": _status,
            "S/N": "",
            "LOCATION": "",
            "2nd floor Location": "",
            "Source Room": "Basement 1",
            "Employee": _employee,
            "FINAL CUSTOMER": _customer,
            "Project Ref. #": _project,
            "Installation Notes": _installation,
            "Movement Date": "2026-01-16",
        }
    )
    _TEMPLATE_SAMPLE_ROWS.append([_sample[column] for column in parsing.COLUMNS])


def build_template_csv():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(parsing.TEMPLATE_COLUMNS)
    for row in _TEMPLATE_SAMPLE_ROWS:
        writer.writerow(row)
    return buffer.getvalue()


def build_template_xlsx():
    """The .xlsx counterpart to build_template_csv() — same columns/sample
    rows, for operators who'd rather round-trip through Excel than CSV
    (both upload formats are already supported by apps.imports.parsing).
    """
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Import Template"
    sheet.append(parsing.TEMPLATE_COLUMNS)
    for row in _TEMPLATE_SAMPLE_ROWS:
        sheet.append(row)

    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="163B50")
        sheet.column_dimensions[cell.column_letter].width = max(20, min(len(cell.value) + 3, 38))
    for column, choices in (
        ("Status", "In Stock,Assigned,Delivered,In Use"),
        ("Tracking Method", "unit,quantity"),
        ("Stock Purpose", "Internal,Customer"),
    ):
        validation = DataValidation(type="list", formula1=f'"{choices}"', allow_blank=True)
        validation.showErrorMessage = True
        validation.errorTitle = "Choose a listed value"
        validation.error = "Use the dropdown options."
        sheet.add_data_validation(validation)
        letter = get_column_letter(parsing.COLUMNS.index(column) + 1)
        validation.add(f"{letter}2:{letter}1048576")
    instructions = workbook.create_sheet("Instructions")
    for instruction in (
        "Replace the five example rows before importing. Keep the header row unchanged.",
        "Status: In Stock, Assigned, Delivered, or In Use. "
        "Blank LOCATION without Status requires preview review.",
        "In Stock: LOCATION is the current storage room. "
        "Shelf/Rack is the optional sub-location.",
        "Assigned: Employee, Movement Date and Source Room are required.",
        "Delivered: FINAL CUSTOMER, Project Ref. #, Movement Date and Source Room are required.",
        "In Use: Internal Stock Purpose, unit Tracking Method, Installation Notes, "
        "Movement Date and Source Room are required.",
        "Source Room is the original receipt room, not the installed or delivered destination. "
        "A batch default may supply it.",
        "Tracking Method: unit for individual items (QTY 1, serial optional); "
        "quantity for bulk items (positive QTY, no serial).",
        "Use YYYY-MM-DD dates. Arrival Date must not be after Movement Date. "
        "Existing product tracking cannot change.",
        "Reserved, Returned, Damaged, Lost and Disposed are not supported import statuses; "
        "resolve these separately.",
        "Preview every row before execution. Duplicate serials require explicit acknowledgment. "
        "Imported rows are not repeated on retry.",
    ):
        instructions.append([instruction])
    instructions.column_dimensions["A"].width = 145

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_results_csv(batch):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Row", "Outcome", "Detail", "Brand", "Model", "Serial", "Stock Purpose"])
    for row in batch.rows.all():
        writer.writerow(
            spreadsheet_safe_row(
                [
                    row.row_number,
                    row.get_outcome_display(),
                    row.outcome_detail,
                    row.normalized_data.get("brand_name", ""),
                    row.normalized_data.get("model", ""),
                    row.normalized_data.get("vendor_serial", ""),
                    row.normalized_data.get("stock_purpose", ""),
                ]
            )
        )
    return buffer.getvalue()
