"""Scheduled integrity checks for stored generated-document PDFs — a
host-cron-invoked management command (check_document_integrity), the same
pattern already used for the daily notification digest and the nightly
Excel export. Surfaces a missing/corrupt file to an Administrator via the
PDF health diagnostics page before a user discovers it by clicking Download
and getting a 404.
"""

from django.utils import timezone

from .models import DocumentIntegrityCheckRun, GeneratedDocument


def _is_valid_pdf(pdf_file):
    """Exists, is readable, and starts with the PDF magic bytes — cheap
    checks that catch the two failure modes that actually happen (a missing
    file, or a zero-byte/truncated one from a prior storage failure) without
    fully parsing every PDF on every run.
    """
    try:
        with pdf_file.open("rb") as f:
            header = f.read(4)
    except OSError:
        return False
    return header == b"%PDF"


def check_document_integrity():
    """Walks every GeneratedDocument and confirms its pdf_file is still a
    real, readable PDF in storage. Records one DocumentIntegrityCheckRun row
    per invocation (append-only history of check runs, not just the latest
    result) and returns it.
    """
    started_at = timezone.now()
    missing_ids = []
    checked_count = 0
    for document in GeneratedDocument.objects.only("id", "pdf_file").iterator():
        checked_count += 1
        if not _is_valid_pdf(document.pdf_file):
            missing_ids.append(str(document.pk))

    return DocumentIntegrityCheckRun.objects.create(
        started_at=started_at,
        finished_at=timezone.now(),
        checked_count=checked_count,
        missing_document_ids=missing_ids,
    )
