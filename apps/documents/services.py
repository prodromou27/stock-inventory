from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import connection, transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role
from apps.inventory.access import require_transaction_access
from apps.inventory.models import MovementType

from .models import Attachment, GeneratedDocument
from .pdf import CURRENT_TEMPLATE_VERSION, build_document_context, document_type_for, render_pdf

# --- Document generation ---------------------------------------------------


def next_document_number():
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('document_number_seq')")
        value = cursor.fetchone()[0]
    return f"DOC-{value:06d}"


def generate_document(*, txn, user, supersedes=None):
    """Renders and persists a PDF snapshot of a completed assignment/delivery
    transaction (spec §10, acceptance criterion §21.13). `supersedes`, when
    given, links to the GeneratedDocument this one replaces — regeneration
    never edits or removes the old row/file (doc 06).
    """
    stored_file = None
    storage = None
    try:
        with transaction.atomic():
            require_role(user, ADMINISTRATOR, STOCK_MANAGER)
            require_transaction_access(user, txn)

            if txn.movement_type not in (MovementType.ASSIGNMENT, MovementType.DELIVERY):
                raise ValidationError(
                    "Only assignment or delivery transactions can generate a printable document."
                )

            document_number = next_document_number()
            document_type = document_type_for(txn)
            context = build_document_context(transaction=txn, document_number=document_number)
            pdf_bytes = render_pdf(context, document_type=document_type)

            document = GeneratedDocument(
                transaction=txn,
                document_number=document_number,
                document_type=document_type,
                template_version=CURRENT_TEMPLATE_VERSION,
                context_snapshot=context,
                generated_by=user,
                supersedes=supersedes,
            )
            document.pdf_file.save(f"{document.id}.pdf", ContentFile(pdf_bytes), save=False)
            stored_file = document.pdf_file.name
            storage = document.pdf_file.storage
            document.full_clean()
            document.save()

            record_event(
                actor=user,
                event_type=AuditEvent.EventType.DOCUMENT_GENERATED,
                obj=document,
                summary=(
                    f"Generated {document.document_type} document {document.document_number} "
                    f"for {txn.transaction_number}"
                ),
            )
        return document
    except Exception:
        if stored_file and storage:
            storage.delete(stored_file)
        raise


@transaction.atomic
def regenerate_document(*, previous_document, user):
    """Creates a new GeneratedDocument for the same transaction, linked back
    via `supersedes`. The previous document's row and PDF file are untouched.
    """
    return generate_document(
        txn=previous_document.transaction, user=user, supersedes=previous_document
    )


# --- Attachments -------------------------------------------------------

ALLOWED_ATTACHMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_ATTACHMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

_SIGNATURES = (
    (b"%PDF", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def _sniff_content_type(uploaded_file):
    """Never trusts the client-supplied Content-Type — reads the file's
    magic bytes directly (spec §17: "validate uploads... never trusting the
    client Content-Type").
    """
    uploaded_file.seek(0)
    header = uploaded_file.read(16)
    uploaded_file.seek(0)
    for signature, content_type in _SIGNATURES:
        if header.startswith(signature):
            return content_type
    return None


def upload_attachment(*, txn, uploaded_file, user):
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    require_transaction_access(user, txn)

    original_filename = uploaded_file.name or ""
    ext = "." + original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise ValidationError(
            f"File type '{ext or 'unknown'}' is not allowed. Allowed: "
            f"{', '.join(sorted(ALLOWED_ATTACHMENT_EXTENSIONS))}."
        )

    if uploaded_file.size > MAX_ATTACHMENT_SIZE_BYTES:
        max_mb = MAX_ATTACHMENT_SIZE_BYTES // (1024 * 1024)
        raise ValidationError(f"File exceeds the maximum allowed size of {max_mb} MB.")

    sniffed_content_type = _sniff_content_type(uploaded_file)
    if sniffed_content_type is None:
        raise ValidationError(
            "File content does not match an allowed file type (PDF, JPEG, or PNG)."
        )

    stored_file = None
    storage = None
    try:
        with transaction.atomic():
            attachment = Attachment(
                transaction=txn,
                original_filename=original_filename,
                content_type=sniffed_content_type,
                size_bytes=uploaded_file.size,
                uploaded_by=user,
            )
            attachment.file.save(f"{attachment.id}{ext}", uploaded_file, save=False)
            stored_file = attachment.file.name
            storage = attachment.file.storage
            attachment.full_clean()
            attachment.save()

            record_event(
                actor=user,
                event_type=AuditEvent.EventType.ATTACHMENT_UPLOADED,
                obj=attachment,
                summary=f"Uploaded attachment '{original_filename}' to {txn.transaction_number}",
            )
        return attachment
    except Exception:
        if stored_file and storage:
            storage.delete(stored_file)
        raise


@transaction.atomic
def delete_attachment(*, attachment, user, reason=""):
    require_role(user, ADMINISTRATOR)
    if attachment.is_deleted:
        raise ValidationError("Attachment is already deleted.")

    attachment.is_deleted = True
    attachment.save(update_fields=["is_deleted"])

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.ATTACHMENT_DELETED,
        obj=attachment,
        summary=f"Deleted attachment '{attachment.original_filename}'"
        + (f" ({reason})" if reason else ""),
    )
    return attachment
