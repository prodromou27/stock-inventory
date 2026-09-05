"""AuditEvent — the append-only audit trail described in
docs/architecture/02-data-model.md and docs/architecture/08-nonfunctional-plan.md.

Append-only is enforced by AppendOnlyModel/AppendOnlyQuerySet (apps.core.models)
at the ORM layer. Full defense-in-depth via a separate, low-privilege runtime
database role with UPDATE/DELETE revoked is a Phase 8 hardening item — see
that base class's docstring for why a same-role REVOKE wouldn't help today.
"""

from django.conf import settings
from django.db import models

from apps.core.models import AppendOnlyModel, AppendOnlyQuerySet, UUIDPrimaryKeyModel


class AuditEvent(UUIDPrimaryKeyModel, AppendOnlyModel):
    class EventType(models.TextChoices):
        LOGIN_SUCCESS = "login_success", "Login success"
        LOGIN_FAILURE = "login_failure", "Login failure"
        RECORD_CREATED = "record_created", "Record created"
        RECORD_UPDATED = "record_updated", "Record updated"
        MOVEMENT_COMPLETED = "movement_completed", "Movement completed"
        DUPLICATE_SERIAL_ACKNOWLEDGED = (
            "duplicate_serial_acknowledged",
            "Duplicate serial acknowledged",
        )
        DUPLICATE_PRODUCT_ACKNOWLEDGED = (
            "duplicate_product_acknowledged",
            "Duplicate product acknowledged",
        )
        DOCUMENT_GENERATED = "document_generated", "Document generated"
        ATTACHMENT_UPLOADED = "attachment_uploaded", "Attachment uploaded"
        ATTACHMENT_DELETED = "attachment_deleted", "Attachment deleted"
        IMPORT_EXECUTED = "import_executed", "Import executed"
        EXPORT_EXECUTED = "export_executed", "Export executed"
        PERMISSION_CHANGED = "permission_changed", "Permission changed"
        ADMIN_CORRECTION = "admin_correction", "Administrator correction"
        ADMIN_REVERSAL = "admin_reversal", "Administrator reversal"
        STOCK_PURPOSE_CHANGED = "stock_purpose_changed", "Stock purpose changed"
        COMPONENT_ASSOCIATION_CHANGED = (
            "component_association_changed",
            "Component association changed",
        )
        DATA_QUALITY_FINDING_RESOLVED = (
            "data_quality_finding_resolved",
            "Data quality finding resolved",
        )

    occurred_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.PROTECT,
        related_name="audit_events",
    )
    event_type = models.CharField(max_length=40, choices=EventType.choices)
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    summary = models.TextField(blank=True)
    old_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["object_type", "object_id"], name="audit_object_idx"),
            models.Index(fields=["actor"], name="audit_actor_idx"),
            models.Index(fields=["event_type"], name="audit_event_type_idx"),
            models.Index(fields=["occurred_at"], name="audit_occurred_at_idx"),
        ]

    def __str__(self):
        return f"{self.get_event_type_display()} — {self.summary}"[:120]
