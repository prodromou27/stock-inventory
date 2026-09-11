import os
import re

from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel, UserStampedModel, UUIDPrimaryKeyModel

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _logo_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"system_settings/logo{ext}"


class SystemSettings(TimestampedModel):
    """Singleton (always pk=1) — Administrator-configurable branding,
    host-allowlist override, business timezone, and outgoing-email (SMTP)
    configuration, consolidated with the rest of the app's configuration
    screens under the Settings hub (see apps.settings.views).

    Not append-only: live configuration, meant to be edited, unlike the
    ledger/audit tables.
    """

    site_name = models.CharField(max_length=100, blank=True, default="Invenio")
    logo = models.FileField(upload_to=_logo_upload_path, null=True, blank=True)
    accent_color = models.CharField(
        max_length=7,
        blank=True,
        default="",
        help_text="Hex color (e.g. #2563eb) used for primary buttons, links, and focus rings "
        "app-wide. Leave blank for the built-in default blue.",
    )
    allowed_hosts_override = models.CharField(
        max_length=1000,
        blank=True,
        help_text=(
            "Comma-separated hostnames. Leave blank to keep the ALLOWED_HOSTS value from "
            ".env.production (wildcarded by default). Setting this REPLACES that value for "
            "every request going forward — a wrong value here can lock you out of the site; "
            "see docs/architecture/09-delivery-backlog.md's Settings section for the recovery "
            "command if that happens."
        ),
    )
    timezone = models.CharField(
        max_length=63,
        blank=True,
        default="",
        help_text='IANA zone name (e.g. "America/New_York"). Leave blank to use the server\'s '
        "configured default (currently the TIME_ZONE environment variable).",
    )
    smtp_host = models.CharField(max_length=255, blank=True, default="")
    # PositiveIntegerField, not a smaller/port-range-checked field: 587/465/25
    # are the only ports anyone will realistically enter, and Django has no
    # built-in "port number" field type worth reaching for over this.
    smtp_port = models.PositiveIntegerField(null=True, blank=True, default=587)
    smtp_username = models.CharField(max_length=255, blank=True, default="")
    # Plaintext in the DB, deliberately — this app has no secrets vault, and
    # building one wasn't asked for; same honesty already applied to
    # allowed_hosts_override's risk callout above. Never included in an
    # audit event's old_values/new_values (see services.update_smtp_settings).
    smtp_password = models.CharField(max_length=255, blank=True, default="")
    smtp_use_tls = models.BooleanField(default=True)
    smtp_from_email = models.CharField(max_length=255, blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = "system settings"
        verbose_name_plural = "system settings"

    def __str__(self):
        return "System settings"

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError

        errors = {}
        if self.accent_color and not _HEX_COLOR_RE.match(self.accent_color):
            errors["accent_color"] = "Enter a color as #rrggbb."
        if self.timezone:
            import zoneinfo

            if self.timezone not in zoneinfo.available_timezones():
                errors["timezone"] = "Unrecognized timezone."
        if errors:
            raise ValidationError(errors)

    @classmethod
    def load(cls):
        """A single plain SELECT, never a write — this is read on every
        request (apps.settings.middleware, apps.settings.context_processors),
        so get_or_create()'s extra SELECT+INSERT/savepoint queries on a cache
        miss aren't acceptable here. An unsaved instance with pk=1 and the
        field defaults stands in until an Administrator actually saves
        something; .save() on it then INSERTs (Django's default save()
        UPDATEs first, falls back to INSERT when that affects 0 rows).
        """
        return cls.objects.first() or cls(pk=1)

    @property
    def allowed_hosts_list(self):
        return [host.strip() for host in self.allowed_hosts_override.split(",") if host.strip()]


class NotificationSubscription(UUIDPrimaryKeyModel, UserStampedModel):
    """Daily digest preferences for one recipient and one country."""

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="inventory_notification_subscriptions",
    )
    country = models.ForeignKey(
        "locations.Location", on_delete=models.PROTECT, related_name="notification_subscriptions"
    )
    is_active = models.BooleanField(default=True)
    notify_low_stock = models.BooleanField(default=True)
    notify_overdue_assignments = models.BooleanField(default=True)
    notify_import_export_failures = models.BooleanField(default=True)
    notify_data_quality = models.BooleanField(default=True)
    mandatory_low_stock = models.BooleanField(default=False)
    mandatory_overdue_assignments = models.BooleanField(default=False)
    mandatory_import_export_failures = models.BooleanField(default=False)
    mandatory_data_quality = models.BooleanField(default=False)
    user_notify_low_stock = models.BooleanField(default=True)
    user_notify_overdue_assignments = models.BooleanField(default=True)
    user_notify_import_export_failures = models.BooleanField(default=True)
    user_notify_data_quality = models.BooleanField(default=True)

    def category_enabled(self, category):
        """Effective preference: Administrator scope plus user choice/mandatory override."""
        admin_enabled = getattr(self, f"notify_{category}")
        mandatory = getattr(self, f"mandatory_{category}")
        user_enabled = getattr(self, f"user_notify_{category}")
        return admin_enabled and (mandatory or user_enabled)

    class Meta:
        ordering = ["country__name", "recipient__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "country"], name="notification_unique_recipient_country"
            )
        ]

    def __str__(self):
        return f"{self.recipient} — {self.country}"


class NotificationDigestDelivery(UUIDPrimaryKeyModel, TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        NO_CONTENT = "no_content", "No content"
        FAILED = "failed", "Failed"

    subscription = models.ForeignKey(
        NotificationSubscription, on_delete=models.CASCADE, related_name="deliveries"
    )
    digest_date = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices)
    item_counts = models.JSONField(default=dict, blank=True)
    detail = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-digest_date", "subscription__country__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "digest_date"],
                name="notification_unique_subscription_digest_date",
            )
        ]


class NotificationCategory(models.TextChoices):
    """Mirrors NotificationSubscription's four notify_* toggles and
    build_digest()'s `counts` dict keys exactly — apps.settings.notifications.
    sync_in_app_notifications() creates one Notification per truthy count
    per daily digest run, reusing that same detection logic rather than
    re-querying it here.
    """

    LOW_STOCK = "low_stock", "Low stock"
    OVERDUE_ASSIGNMENTS = "overdue_assignments", "Overdue temporary assignments"
    IMPORT_EXPORT_FAILURES = "import_export_failures", "Import/export failures"
    DATA_QUALITY = "high_data_quality", "Data quality"


class Notification(UUIDPrimaryKeyModel, TimestampedModel):
    """The in-app bell's backing store — one row per (recipient, country,
    category, day) a daily digest run found something worth surfacing.
    Deliberately NOT one row per individual low-stock product/overdue
    assignment/etc: that would flood the bell with dozens of near-duplicate
    entries every day the underlying issue persists. One summarizing row
    per category per day, linking through to the page that shows the full
    detail, matches how the email digest itself groups the same data into
    sections.

    `recipient` is denormalized from `delivery.subscription.recipient` (and
    `country` from `delivery.subscription.country`) purely so the bell's
    per-request unread-count/list queries never need to join through
    NotificationDigestDelivery -> NotificationSubscription just to filter by
    who's asking — the same "cheap to query on every page" trade doc 02
    already accepts elsewhere (e.g. DataQualityFinding.country).
    """

    delivery = models.ForeignKey(
        NotificationDigestDelivery, on_delete=models.CASCADE, related_name="notifications"
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    country = models.ForeignKey("locations.Location", on_delete=models.CASCADE, related_name="+")
    category = models.CharField(max_length=30, choices=NotificationCategory.choices)
    summary = models.CharField(max_length=255)
    url = models.CharField(max_length=255, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["delivery", "category"], name="unique_notification_per_delivery_category"
            )
        ]
        indexes = [
            models.Index(fields=["recipient", "read_at"], name="notif_recipient_read_idx"),
        ]

    def __str__(self):
        return f"{self.recipient} — {self.get_category_display()} ({self.created_at:%Y-%m-%d})"
