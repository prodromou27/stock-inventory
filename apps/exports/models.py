from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel


class ExportSchedule(models.TextChoices):
    DISABLED = "disabled", "Disabled"
    NIGHTLY = "nightly", "Nightly"
    WEEKLY = "weekly", "Weekly"


class ExportRunStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class ExportSettings(TimestampedModel):
    """Singleton (always pk=1) — Administrator-configurable destination for the
    scheduled full-inventory Excel snapshot (a human-readable safety net
    alongside deploy/backup.sh's pg_dump, per the user's request: an
    Administrator should be able to point this at a local or network path
    without touching deployment config). apps/exports/services.py is the only
    writer; apps/exports/management/commands/run_scheduled_export.py is the
    intended cron entry point (see deploy/DEPLOYMENT.md), mirroring how
    backup.sh itself is invoked.
    """

    export_path = models.CharField(max_length=500, blank=True)
    schedule = models.CharField(
        max_length=10, choices=ExportSchedule.choices, default=ExportSchedule.DISABLED
    )
    weekly_weekday = models.PositiveSmallIntegerField(
        default=6,
        help_text="0=Monday .. 6=Sunday (Python's date.weekday()); only used when schedule=weekly.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run_status = models.CharField(max_length=10, choices=ExportRunStatus.choices, blank=True)
    last_run_detail = models.TextField(blank=True)

    class Meta:
        verbose_name = "export settings"
        verbose_name_plural = "export settings"

    def __str__(self):
        return f"Export settings ({self.get_schedule_display()})"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
