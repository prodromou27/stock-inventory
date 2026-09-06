from django.core.management.base import BaseCommand

from apps.documents.integrity import check_document_integrity


class Command(BaseCommand):
    help = "Verify every generated document's PDF is still present and readable in storage."

    def handle(self, *args, **options):
        run = check_document_integrity()
        if run.missing_count:
            self.stdout.write(
                self.style.WARNING(
                    f"Document integrity check: {run.missing_count} of {run.checked_count} "
                    "documents have a missing or corrupt PDF file."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Document integrity check: all {run.checked_count} documents OK."
                )
            )
