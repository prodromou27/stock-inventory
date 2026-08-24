import os

from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.core.models import AppendOnlyModel, AppendOnlyQuerySet, UUIDPrimaryKeyModel


def _document_upload_path(instance, filename):
    # Storage name is derived from the row's own id, never the caller-supplied
    # filename — docs/architecture/06-documents-and-snapshots.md.
    return f"documents/{instance.id}.pdf"


def _attachment_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"attachments/{instance.id}{ext}"


class DocumentType(models.TextChoices):
    ASSIGNMENT = "assignment", "Assignment"
    DELIVERY = "delivery", "Delivery"


class GeneratedDocument(UUIDPrimaryKeyModel, AppendOnlyModel):
    """A PDF snapshot of a completed assignment/delivery transaction. Never
    updated in place — "regenerate" creates a new row with a fresh
    document_number (doc 02/06); the old PDF file and row are untouched.
    """

    transaction = models.ForeignKey(
        "inventory.InventoryTransaction",
        on_delete=models.PROTECT,
        related_name="generated_documents",
    )
    document_number = models.CharField(max_length=20, unique=True, editable=False)
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    template_version = models.CharField(max_length=40)
    context_snapshot = models.JSONField()
    pdf_file = models.FileField(upload_to=_document_upload_path)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="generated_documents"
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by"
    )

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["transaction"], name="gendoc_transaction_idx"),
        ]

    def __str__(self):
        return self.document_number

    def get_absolute_url(self):
        return reverse("documents:document_detail", kwargs={"pk": self.pk})


class Attachment(UUIDPrimaryKeyModel):
    """A scanned signed form (or other file) linked to a transaction. Not
    append-only — `is_deleted` is a legitimate later mutation — but every
    other field is set once at upload and never changed; a re-upload is
    always a new row (doc 02/06 — "never overwrite an existing attachment
    silently").
    """

    transaction = models.ForeignKey(
        "inventory.InventoryTransaction", on_delete=models.PROTECT, related_name="attachments"
    )
    file = models.FileField(upload_to=_attachment_upload_path)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_attachments"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["transaction"], name="attachment_transaction_idx"),
        ]

    def __str__(self):
        return self.original_filename

    def get_absolute_url(self):
        return reverse("documents:attachment_download", kwargs={"pk": self.pk})
