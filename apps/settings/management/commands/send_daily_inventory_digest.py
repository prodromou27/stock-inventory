from django.core.management.base import BaseCommand

from apps.settings.notifications import send_daily_digests


class Command(BaseCommand):
    help = "Send each active, per-country inventory notification digest once for today."

    def handle(self, *args, **options):
        results = send_daily_digests()
        self.stdout.write(
            self.style.SUCCESS(
                "Daily inventory digests: "
                f"{results['sent']} sent, {results['no_content']} empty, "
                f"{results['failed']} failed, {results['skipped']} already processed."
            )
        )
