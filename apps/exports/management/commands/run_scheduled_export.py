from django.core.management.base import BaseCommand, CommandError

from apps.exports.models import ExportSettings
from apps.exports.services import run_export, should_run_today


class Command(BaseCommand):
    help = (
        "Writes a full-inventory Excel snapshot to the Administrator-configured export path, "
        "if today matches the configured schedule (deploy/DEPLOYMENT.md — intended to be "
        "invoked daily by cron, same as deploy/backup.sh; it decides internally whether today "
        "is a run day)."
    )

    def handle(self, *args, **options):
        settings_obj = ExportSettings.load()
        if not should_run_today(settings_obj):
            self.stdout.write("Scheduled export: nothing to do today.")
            return

        try:
            path = run_export(user=None)
        except Exception as exc:
            raise CommandError(f"Scheduled export failed: {exc}") from exc

        self.stdout.write(self.style.SUCCESS(f"Scheduled export written to {path}"))
