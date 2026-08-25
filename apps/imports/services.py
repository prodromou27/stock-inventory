import csv
import datetime
import io

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.catalog.models import Brand, Product, TrackingMethod
from apps.catalog.services import check_duplicate_products, create_product
from apps.core.authorization import ADMINISTRATOR, require_role
from apps.core.text import normalize_whitespace
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


# --- Upload + staging --------------------------------------------------


@transaction.atomic
def create_batch_from_upload(*, uploaded_file, user):
    """Parses the file, creates the ImportBatch, and stages every row
    (doc 07 steps 1-4 collapsed into one pass — each row is independent and
    validation is cheap, so there's no benefit to a separate DB round trip
    per stage for a first version of this pipeline).
    """
    require_role(user, ADMINISTRATOR)

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
    )
    batch.file.save(uploaded_file.name, ContentFile(file_bytes), save=False)
    batch.full_clean(exclude=["file"])
    batch.save()

    warning_count = 0
    failed_count = 0
    import_rows = []
    for row_number, raw in rows:
        normalized, outcome, detail = _stage_row(raw)
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


def _stage_row(raw):
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

    tracking_method = TrackingMethod.UNIT if serial_text else TrackingMethod.QUANTITY
    if tracking_method == TrackingMethod.QUANTITY and (quantity is None or quantity <= 0):
        issues.append("Quantity-tracked rows (no serial number given) need a positive QTY.")

    conflict = _tracking_method_conflict(brand_name, model, tracking_method)
    if conflict:
        issues.append(conflict)

    location_text = normalize_text(raw.get("LOCATION"))
    sub_location_text = normalize_text(raw.get("2nd floor Location"))
    resolved_location, location_detail = resolve_location(location_text, sub_location_text)
    if resolved_location is None:
        warnings.append(location_detail or f"Unknown location '{location_text}'.")

    arrival_date, arrival_valid = parse_legacy_date(raw.get("Arrival Date"))
    if not arrival_valid:
        warnings.append(
            f"Arrival Date '{raw.get('Arrival Date')}' could not be parsed; "
            "today's date will be used."
        )

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
        "sub_location_text": sub_location_text,
        "resolved_location_id": str(resolved_location.pk) if resolved_location else None,
        "location_override_id": None,
        "project_reference": normalize_text(raw.get("Project Ref. #")),
        "final_customer": normalize_text(raw.get("FINAL CUSTOMER")),
        "arrival_date": arrival_date.isoformat() if arrival_date else None,
        "notes": notes,
        "duplicate_serial_ids": [str(pk) for pk in duplicate_serial_ids],
        "duplicate_product_ids": [str(pk) for pk in duplicate_product_ids],
    }

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
    _require_editable_batch(row.batch)

    normalized = dict(row.normalized_data)
    normalized["location_override_id"] = str(location.pk)
    row.normalized_data = normalized
    row.outcome_detail = f"{row.outcome_detail} (location manually set to {location})".strip()
    row.save(update_fields=["normalized_data", "outcome_detail"])
    return row


@transaction.atomic
def skip_row(*, row, user):
    require_role(user, ADMINISTRATOR)
    _require_editable_batch(row.batch)
    if row.outcome == ImportRowOutcome.SKIPPED:
        return row
    row.outcome = ImportRowOutcome.SKIPPED
    row.outcome_detail = "Skipped by user during preview."
    row.save(update_fields=["outcome", "outcome_detail"])
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

    batch.status = ImportBatchStatus.EXECUTING
    batch.save(update_fields=["status"])

    rows = list(
        batch.rows.filter(
            outcome__in=[ImportRowOutcome.PENDING, ImportRowOutcome.WARNING]
        ).order_by("row_number")
    )
    for start in range(0, len(rows), EXECUTE_BATCH_SIZE):
        _execute_row_chunk(rows[start : start + EXECUTE_BATCH_SIZE], user=user)

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
    location_id = data.get("location_override_id") or data.get("resolved_location_id")
    if not location_id:
        row.outcome_detail = (
            f"{row.outcome_detail} Not executed: no resolved or overridden location.".strip()
        )
        row.save(update_fields=["outcome_detail"])
        return

    from apps.locations.models import Location

    try:
        location = Location.objects.get(pk=location_id)
        product = _get_or_create_import_product(
            user=user,
            brand_name=data["brand_name"],
            model=data["model"],
            product_type_name=data["product_type_name"],
            tracking_method=data["tracking_method"],
        )
        arrival_date = (
            datetime.date.fromisoformat(data["arrival_date"])
            if data.get("arrival_date")
            else timezone.localdate()
        )
        txn = receive_stock(
            user=user,
            product=product,
            location=location,
            occurred_at=arrival_date,
            vendor_serial=data["vendor_serial"],
            quantity=data["quantity"]
            or (1 if data["tracking_method"] == TrackingMethod.UNIT else None),
            project_reference=data["project_reference"],
            final_customer=data["final_customer"],
            notes=data["notes"],
            duplicate_serial_acknowledged=True,
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


def _get_or_create_import_product(*, user, brand_name, model, product_type_name, tracking_method):
    """create_product() always inserts a new Product row, even when
    duplicate_acknowledged=True — that's correct for the interactive
    "I know this looks like a duplicate but I mean it" flow, but wrong here:
    every row in a batch that shares the same brand/model must resolve to
    the *same* Product, not a fresh duplicate per row.
    """
    normalized_model = normalize_whitespace(model).lower()
    brand = Brand.objects.filter(name__iexact=brand_name).first()
    if brand:
        existing = Product.objects.filter(
            brand=brand, normalized_model=normalized_model, sku=""
        ).first()
        if existing:
            return existing

    return create_product(
        user=user,
        brand_name=brand_name,
        model=model,
        product_type_name=product_type_name,
        tracking_method=tracking_method,
        duplicate_acknowledged=True,
    )


# --- Downloads --------------------------------------------------------


def build_template_csv():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(parsing.COLUMNS)
    writer.writerow(
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
        ]
    )
    writer.writerow(
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
        ]
    )
    return buffer.getvalue()


def build_results_csv(batch):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Row", "Outcome", "Detail", "Brand", "Model", "Serial"])
    for row in batch.rows.all():
        writer.writerow(
            [
                row.row_number,
                row.get_outcome_display(),
                row.outcome_detail,
                row.normalized_data.get("brand_name", ""),
                row.normalized_data.get("model", ""),
                row.normalized_data.get("vendor_serial", ""),
            ]
        )
    return buffer.getvalue()
