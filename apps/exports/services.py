import os
from datetime import date, datetime
from datetime import timezone as dt_timezone

import openpyxl
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, require_role
from apps.inventory.models import StockBalance, UnitAsset

from .models import ExportRunStatus, ExportSchedule, ExportSettings

ASSET_HEADERS = [
    "Brand",
    "Model",
    "SKU",
    "Type",
    "Serial",
    "Status",
    "Location",
    "Project Reference",
    "Final Customer",
    "Supplier",
    "Arrival Date",
    "Removal Date",
]

BALANCE_HEADERS = ["Brand", "Model", "SKU", "Type", "Location", "On Hand", "Reserved", "Available"]


@transaction.atomic
def update_settings(*, user, export_path, schedule, weekly_weekday, validate_path=True):
    """Administrator-only. `validate_path` (best-effort directory
    create-and-write-test) is skipped only by tests that don't need real
    filesystem access — always on for the real form/view.
    """
    require_role(user, ADMINISTRATOR)

    export_path = (export_path or "").strip()
    if schedule != ExportSchedule.DISABLED and not export_path:
        raise ValidationError("An export path is required when scheduling is enabled.")

    if export_path and validate_path:
        _check_path_writable(export_path)

    settings_obj = ExportSettings.load()
    old_values = {
        "export_path": settings_obj.export_path,
        "schedule": settings_obj.schedule,
        "weekly_weekday": settings_obj.weekly_weekday,
    }
    settings_obj.export_path = export_path
    settings_obj.schedule = schedule
    settings_obj.weekly_weekday = weekly_weekday
    settings_obj.updated_by = user
    settings_obj.full_clean()
    settings_obj.save()

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=settings_obj,
        summary="Updated scheduled inventory export settings",
        old_values=old_values,
        new_values={
            "export_path": export_path,
            "schedule": schedule,
            "weekly_weekday": weekly_weekday,
        },
    )
    return settings_obj


def _check_path_writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".stock_inventory_write_test")
        with open(probe, "wb") as f:
            f.write(b"ok")
        os.remove(probe)
    except OSError as exc:
        raise ValidationError(f"Export path is not writable: {exc}") from exc


def build_inventory_workbook():
    """A full, unscoped snapshot of current inventory data — every unit
    asset (any status) and every stock balance — as an .xlsx workbook. Not
    scoped by location like the interactive reports: this is a system-level
    backup an Administrator configured, not a user-facing report.
    """
    workbook = openpyxl.Workbook()

    assets_sheet = workbook.active
    assets_sheet.title = "Unit Assets"
    assets_sheet.append(ASSET_HEADERS)
    assets = UnitAsset.objects.select_related(
        "product", "product__brand", "product__product_type", "current_location"
    ).order_by("product__brand__name", "product__model", "vendor_serial")
    for asset in assets:
        assets_sheet.append(
            [
                asset.product.brand.name,
                asset.product.model,
                asset.product.sku,
                asset.product.product_type.name,
                asset.vendor_serial,
                asset.get_status_display(),
                str(asset.current_location or ""),
                asset.project_reference,
                asset.final_customer,
                asset.supplier,
                asset.arrival_date.isoformat() if asset.arrival_date else "",
                asset.last_removal_date.isoformat() if asset.last_removal_date else "",
            ]
        )

    balances_sheet = workbook.create_sheet("Stock Balances")
    balances_sheet.append(BALANCE_HEADERS)
    balances = StockBalance.objects.select_related(
        "product", "product__brand", "product__product_type", "location"
    ).order_by("product__brand__name", "product__model", "location__name")
    for balance in balances:
        balances_sheet.append(
            [
                balance.product.brand.name,
                balance.product.model,
                balance.product.sku,
                balance.product.product_type.name,
                str(balance.location),
                balance.on_hand_quantity,
                balance.reserved_quantity,
                balance.available_quantity,
            ]
        )

    return workbook


def _timestamped_filename():
    return f"stock_inventory_backup_{datetime.now(dt_timezone.utc):%Y%m%dT%H%M%SZ}.xlsx"


def run_export(*, user=None):
    """Builds the workbook and writes it to the configured export_path,
    recording the outcome on ExportSettings and in the audit log either way
    — a failed export (unreachable network path, permission denied, disk
    full) must be visible to an Administrator, not silently swallowed.
    `user=None` is the scheduled/cron path; a real user is a manual
    "run now" trigger from the settings screen.

    Deliberately *not* wrapped in `transaction.atomic` — on failure this
    still needs to persist the failure status and audit event before
    re-raising, which an enclosing atomic block would otherwise roll back.
    """
    settings_obj = ExportSettings.load()
    if not settings_obj.export_path:
        raise ValidationError("No export path is configured.")

    now = datetime.now(dt_timezone.utc)
    try:
        workbook = build_inventory_workbook()
        os.makedirs(settings_obj.export_path, exist_ok=True)
        target = os.path.join(settings_obj.export_path, _timestamped_filename())
        workbook.save(target)
    except OSError as exc:
        settings_obj.last_run_at = now
        settings_obj.last_run_status = ExportRunStatus.FAILED
        settings_obj.last_run_detail = str(exc)
        settings_obj.save(update_fields=["last_run_at", "last_run_status", "last_run_detail"])
        record_event(
            actor=user,
            event_type=AuditEvent.EventType.EXPORT_EXECUTED,
            obj=settings_obj,
            summary="Scheduled inventory export failed",
            new_values={"status": "failed", "detail": str(exc)},
        )
        raise

    settings_obj.last_run_at = now
    settings_obj.last_run_status = ExportRunStatus.SUCCESS
    settings_obj.last_run_detail = f"Wrote {target}"
    settings_obj.save(update_fields=["last_run_at", "last_run_status", "last_run_detail"])
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.EXPORT_EXECUTED,
        obj=settings_obj,
        summary=f"Scheduled inventory export succeeded ({target})",
        new_values={"status": "success", "path": target},
    )
    return target


def should_run_today(settings_obj, *, today=None):
    today = today or date.today()
    if settings_obj.schedule == ExportSchedule.DISABLED:
        return False
    if settings_obj.schedule == ExportSchedule.NIGHTLY:
        return True
    return today.weekday() == settings_obj.weekly_weekday
