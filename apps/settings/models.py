import os

from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel


def _logo_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"system_settings/logo{ext}"


class SystemSettings(TimestampedModel):
    """Singleton (always pk=1) — Administrator-configurable branding and
    host-allowlist override, consolidated with the rest of the app's
    configuration screens under the Settings hub (see apps.settings.views).

    Not append-only: live configuration, meant to be edited, unlike the
    ledger/audit tables.
    """

    site_name = models.CharField(max_length=100, blank=True, default="Stock Inventory")
    logo = models.FileField(upload_to=_logo_upload_path, null=True, blank=True)
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
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = "system settings"
        verbose_name_plural = "system settings"

    def __str__(self):
        return "System settings"

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
