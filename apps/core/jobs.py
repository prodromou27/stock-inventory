import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role

from .models import BackgroundJob

logger = logging.getLogger(__name__)


def _active_duplicate(*, user, task_type, payload):
    return BackgroundJob.objects.filter(
        requested_by=user,
        task_type=task_type,
        payload=payload,
        status__in=(BackgroundJob.Status.QUEUED, BackgroundJob.Status.RUNNING),
    ).first()


def enqueue_job(*, user, task_type, payload):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied("An active user is required.")
    existing = _active_duplicate(user=user, task_type=task_type, payload=payload)
    if existing:
        return existing, False
    try:
        with transaction.atomic():
            job = BackgroundJob.objects.create(
                requested_by=user,
                task_type=task_type,
                payload=payload,
                status_message="Waiting for a worker",
            )
    except IntegrityError:
        # The conditional unique constraint closes the simultaneous double-
        # submit race between the optimistic lookup above and this INSERT.
        existing = _active_duplicate(user=user, task_type=task_type, payload=payload)
        if existing is not None:
            return existing, False
        return enqueue_job(user=user, task_type=task_type, payload=payload)
    return job, True


def enqueue_document(*, user, txn):
    from apps.documents.services import document_type_for
    from apps.inventory.access import require_transaction_access

    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    require_transaction_access(user, txn)
    document_type_for(txn)
    return enqueue_job(
        user=user,
        task_type=BackgroundJob.TaskType.DOCUMENT,
        payload={"transaction_id": str(txn.pk)},
    )


def enqueue_import(*, user, batch, confirm_repeat=False):
    from apps.imports.models import ImportBatchStatus

    require_role(user, ADMINISTRATOR)
    repeated = (
        batch.__class__.objects.filter(
            file_checksum=batch.file_checksum, status=ImportBatchStatus.COMPLETED
        )
        .exclude(pk=batch.pk)
        .exists()
    )
    if repeated and not confirm_repeat:
        raise ValidationError("Confirm this repeated file before queueing the import.")
    return enqueue_job(
        user=user,
        task_type=BackgroundJob.TaskType.IMPORT,
        payload={"batch_id": str(batch.pk), "confirm_repeat": bool(confirm_repeat)},
    )


def enqueue_report(*, user, report, export_format):
    from apps.reporting.services import require_saved_report_access

    require_saved_report_access(report=report, user=user)
    if export_format not in {"csv", "xlsx", "pdf"}:
        raise ValidationError("Choose CSV, Excel, or PDF.")
    return enqueue_job(
        user=user,
        task_type=BackgroundJob.TaskType.REPORT,
        payload={"report_id": str(report.pk), "format": export_format},
    )


def enqueue_inventory_export(*, user):
    require_role(user, ADMINISTRATOR)
    return enqueue_job(user=user, task_type=BackgroundJob.TaskType.INVENTORY_EXPORT, payload={})


def enqueue_notification_refresh(*, user):
    return enqueue_job(user=user, task_type=BackgroundJob.TaskType.NOTIFICATIONS, payload={})


def _execute(job):
    user = job.requested_by
    if not user.is_active:
        raise PermissionDenied("The requesting user is no longer active.")
    payload = job.payload
    if job.task_type == BackgroundJob.TaskType.DOCUMENT:
        from apps.documents.services import generate_document
        from apps.inventory.access import scope_transaction_queryset
        from apps.inventory.models import InventoryTransaction

        txn = scope_transaction_queryset(
            user, InventoryTransaction.objects.filter(pk=payload.get("transaction_id"))
        ).first()
        if txn is None:
            raise PermissionDenied("The transaction is no longer accessible.")
        document = generate_document(txn=txn, user=user)
        return {"result_url": document.get_absolute_url(), "message": document.document_number}
    if job.task_type == BackgroundJob.TaskType.IMPORT:
        from apps.imports.models import ImportBatch
        from apps.imports.services import execute_batch

        require_role(user, ADMINISTRATOR)
        batch = ImportBatch.objects.get(pk=payload.get("batch_id"))
        batch = execute_batch(batch=batch, user=user)
        return {"result_url": batch.get_absolute_url(), "message": batch.get_status_display()}
    if job.task_type == BackgroundJob.TaskType.REPORT:
        from apps.reporting.models import SavedReport
        from apps.reporting.services import build_saved_report_export

        report = SavedReport.objects.get(pk=payload.get("report_id"))
        content, content_type, filename = build_saved_report_export(
            report=report, user=user, export_format=payload.get("format")
        )
        return {
            "content": content,
            "content_type": content_type,
            "filename": filename,
            "message": f"{filename} is ready",
            "audit_report": report,
        }
    if job.task_type == BackgroundJob.TaskType.INVENTORY_EXPORT:
        from apps.exports.services import run_export

        path = run_export(user=user)
        return {"message": f"Export written to {path}"}
    if job.task_type == BackgroundJob.TaskType.NOTIFICATIONS:
        from apps.settings.notifications import refresh_in_app_notifications

        count = refresh_in_app_notifications(user=user, force=True)
        return {
            "result_url": reverse("settings:notification_list"),
            "message": f"Refreshed {count} subscription(s)",
        }
    raise ValidationError("Unknown background task type.")


def process_next_job():
    """Atomically claim one queued row; expensive work runs after the lock is released."""
    with transaction.atomic():
        job = (
            BackgroundJob.objects.select_for_update(skip_locked=True)
            .filter(status=BackgroundJob.Status.QUEUED)
            .order_by("created_at")
            .first()
        )
        if job is None:
            return None
        job.status = BackgroundJob.Status.RUNNING
        job.progress_percent = 5
        job.status_message = "Running"
        job.started_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "progress_percent",
                "status_message",
                "started_at",
                "updated_at",
            ]
        )

    try:
        result = _execute(job)
        content = result.get("content")
        if content is not None:
            job.output_file.save(result["filename"], ContentFile(content), save=False)
            job.output_filename = result["filename"]
            job.output_content_type = result["content_type"]
            job.output_size_bytes = len(content)
            retention_days = getattr(settings, "BACKGROUND_JOB_OUTPUT_RETENTION_DAYS", 7)
            job.output_expires_at = timezone.now() + timedelta(days=retention_days)
        job.result_url = result.get("result_url", "")
        job.status_message = result.get("message", "Completed")[:255]
        job.status = BackgroundJob.Status.SUCCEEDED
        job.progress_percent = 100
        job.completed_at = timezone.now()
        job.error_message = ""
        with transaction.atomic():
            job.save()
            if result.get("audit_report") is not None:
                from apps.audit.models import AuditEvent
                from apps.audit.services import record_event

                record_event(
                    actor=job.requested_by,
                    event_type=AuditEvent.EventType.EXPORT_EXECUTED,
                    obj=result["audit_report"],
                    summary=f"Generated background report export '{job.output_filename}'",
                )
    except Exception as exc:  # noqa: BLE001 - failure is persisted for the requester
        logger.exception("Background job %s failed", job.pk)
        if job.output_file and job.output_file.name:
            try:
                job.output_file.storage.delete(job.output_file.name)
            except OSError:
                logger.exception("Could not clean up failed background job output")
            job.output_file = ""
        job.status = BackgroundJob.Status.FAILED
        job.status_message = "Failed"
        job.error_message = str(exc)[:4000]
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "status_message",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )
    return job


def cleanup_expired_outputs():
    """Remove disposable exports while retaining durable job metadata."""
    queryset = BackgroundJob.objects.filter(
        output_deleted_at__isnull=True,
        output_file__gt="",
        output_expires_at__lte=timezone.now(),
    )
    removed = 0
    for job in queryset.iterator():
        storage = job.output_file.storage
        name = job.output_file.name
        if name and storage.exists(name):
            storage.delete(name)
        job.output_file = ""
        job.output_deleted_at = timezone.now()
        job.save(update_fields=["output_file", "output_deleted_at", "updated_at"])
        removed += 1
    return removed


def fail_stale_jobs(*, older_than=timedelta(hours=6)):
    """Make work abandoned by a terminated worker visible and re-queueable.

    Jobs are not retried automatically because imports and external-path
    exports can have side effects. Their domain services remain idempotent,
    but an explicit user retry is clearer and safer than guessing.
    """
    now = timezone.now()
    return BackgroundJob.objects.filter(
        status=BackgroundJob.Status.RUNNING,
        started_at__lt=now - older_than,
    ).update(
        status=BackgroundJob.Status.FAILED,
        status_message="Worker stopped before completion",
        error_message=(
            "The worker stopped while this job was running. Review the result and retry it."
        ),
        completed_at=now,
        updated_at=now,
    )
