from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel, UUIDPrimaryKeyModel


class DataQualityIssueType(models.TextChoices):
    """The 12 checks apps.dataquality.checks runs. Each maps to exactly one
    check function there — see that module's docstring for what each one
    actually looks for and why.
    """

    DUPLICATE_SERIAL = "duplicate_serial", "Duplicate serial number"
    MISSING_LOCATION = "missing_location", "Missing location"
    MISSING_CUSTODIAN = "missing_custodian", "Missing custodian"
    SERIALIZED_ASSET_WITHOUT_SERIAL = (
        "serialized_asset_without_serial",
        "Serialized asset without a serial number",
    )
    INVALID_BALANCE = "invalid_balance", "Stock balance for an inactive product"
    STALE_CUSTODY_POINTER = "stale_custody_pointer", "Stale custody pointer"
    CUSTOMER_STOCK_MISSING_REFERENCE = (
        "customer_stock_missing_reference",
        "Customer stock missing customer/project reference",
    )
    INACTIVE_LOCATION_WITH_ACTIVE_STOCK = (
        "inactive_location_with_active_stock",
        "Inactive location with active stock",
    )
    INVALID_LOCATION_HIERARCHY = "invalid_location_hierarchy", "Invalid location hierarchy"
    MISSING_PROCUREMENT_INFO = "missing_procurement_info", "Missing procurement information"
    DUPLICATE_PRODUCT = "duplicate_product", "Duplicate or potential-duplicate product"
    ORPHANED_TRANSACTION_REFERENCE = (
        "orphaned_transaction_reference",
        "Orphaned transaction reference",
    )


class DataQualitySeverity(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class DataQualityStatus(models.TextChoices):
    OPEN = "open", "Open"
    RESOLVED = "resolved", "Resolved"
    DISMISSED = "dismissed", "Dismissed"


class DataQualityFinding(UUIDPrimaryKeyModel, TimestampedModel):
    """One detected issue, persisted (not computed live) because "detected"/
    "resolved" dates and a resolution status have no natural "recompute on
    every page view" answer once the underlying data is fixed — the whole
    point is a durable record of what was wrong and when it stopped being
    wrong. Never itself an authorization boundary or a ledger/audit
    replacement: apps.dataquality.services.run_detection() only ever reads
    other apps' data and writes rows here — no InventoryTransaction/
    AuditEvent/notification is ever created just from detecting something,
    only from a correction actually applied through this app's own
    services (which reuse apps.inventory.services.corrections underneath).

    `object_type`/`object_id` follow AuditEvent's existing string-pair
    convention (never a GenericForeignKey — apps.dataquality doesn't need a
    live FK, only a display link built by apps.dataquality.views' small
    per-object_type -> reverse() dispatch table), so a since-deleted object
    doesn't break this row (PROTECT everywhere means that's normally
    impossible anyway, but this keeps the model dependency-free of every
    app it might ever report on).

    `dedup_key` (issue_type + object_type + object_id) is what makes
    re-running detection idempotent: the same real-world issue always
    upserts the same row rather than piling up duplicates every scan.
    """

    dedup_key = models.CharField(max_length=200, unique=True, editable=False)
    issue_type = models.CharField(max_length=40, choices=DataQualityIssueType.choices)
    severity = models.CharField(max_length=10, choices=DataQualitySeverity.choices)
    status = models.CharField(
        max_length=10, choices=DataQualityStatus.choices, default=DataQualityStatus.OPEN
    )
    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    # Denormalized at detection time purely for filtering/display in the
    # workspace list — never re-derived live, so a finding's location
    # context stays legible even if the underlying record later moves.
    country = models.CharField(max_length=120, blank=True)
    location_label = models.CharField(max_length=255, blank=True)
    explanation = models.TextField()
    recommended_correction = models.TextField(blank=True)
    detected_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-severity", "-detected_at"]
        indexes = [
            models.Index(fields=["status"], name="dqfinding_status_idx"),
            models.Index(fields=["issue_type"], name="dqfinding_issue_type_idx"),
            models.Index(fields=["country"], name="dqfinding_country_idx"),
            models.Index(fields=["object_type", "object_id"], name="dqfinding_object_idx"),
        ]

    def __str__(self):
        return f"{self.get_issue_type_display()} — {self.object_type} {self.object_id}"
