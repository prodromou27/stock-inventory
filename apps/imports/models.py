import os

from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.core.models import TimestampedModel, UUIDPrimaryKeyModel


def _import_upload_path(instance, filename):
    # Storage name is derived from the batch's own id, never the caller-supplied
    # filename — matches apps.documents' pattern (docs/architecture/06).
    ext = os.path.splitext(filename)[1].lower()
    return f"imports/{instance.id}{ext}"


class ImportBatchStatus(models.TextChoices):
    UPLOADED = "uploaded", "Uploaded"
    PREVIEWED = "previewed", "Previewed"
    EXECUTING = "executing", "Executing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    PARTIALLY_COMPLETED = "partially_completed", "Partially completed"


class ImportRowOutcome(models.TextChoices):
    PENDING = "pending", "Pending"
    IMPORTED = "imported", "Imported"
    SKIPPED = "skipped", "Skipped"
    WARNING = "warning", "Warning"
    FAILED = "failed", "Failed"


class ImportBatch(UUIDPrimaryKeyModel, TimestampedModel):
    """docs/architecture/02-data-model.md's ImportBatch/ImportRow, and
    docs/architecture/07-excel-import.md's pipeline. `ImportRow` rows are
    hard-deletable staging data (deletion policy summary, doc 02) — only
    once a row has been executed does its resulting UnitAsset/
    InventoryTransaction become the permanent, append-only record.
    """

    source_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to=_import_upload_path)
    file_checksum = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="import_batches"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20, choices=ImportBatchStatus.choices, default=ImportBatchStatus.UPLOADED
    )
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="executed_import_batches",
    )
    executed_at = models.DateTimeField(null=True, blank=True)
    imported_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    warning_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"Import {self.source_filename} ({self.uploaded_at:%Y-%m-%d})"

    def get_absolute_url(self):
        return reverse("imports:batch_detail", kwargs={"pk": self.pk})

    def row_count(self):
        return self.rows.count()


class ImportRow(UUIDPrimaryKeyModel):
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="rows")
    row_number = models.PositiveIntegerField()
    raw_data = models.JSONField()
    normalized_data = models.JSONField(default=dict, blank=True)
    outcome = models.CharField(
        max_length=20, choices=ImportRowOutcome.choices, default=ImportRowOutcome.PENDING
    )
    outcome_detail = models.TextField(blank=True)
    created_unit_asset = models.ForeignKey(
        "inventory.UnitAsset", null=True, blank=True, on_delete=models.PROTECT
    )
    created_transaction = models.ForeignKey(
        "inventory.InventoryTransaction", null=True, blank=True, on_delete=models.PROTECT
    )

    class Meta:
        ordering = ["row_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "row_number"], name="importrow_unique_batch_row"
            )
        ]
        indexes = [
            models.Index(fields=["batch", "outcome"], name="importrow_batch_outcome_idx"),
        ]

    def __str__(self):
        return f"Row {self.row_number} of {self.batch_id}"
