from django.core.management.base import BaseCommand

from apps.core.jobs import cleanup_expired_outputs


class Command(BaseCommand):
    help = "Delete expired temporary job output files while retaining job history."

    def handle(self, *args, **options):
        removed = cleanup_expired_outputs()
        self.stdout.write(self.style.SUCCESS(f"Removed {removed} expired job output file(s)."))
