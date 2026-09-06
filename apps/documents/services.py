import logging
import time

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import connection, transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role
from apps.inventory.access import require_transaction_access
from apps.inventory.models import MovementType
from apps.locations.scoping import country_for_location

from .models import Attachment, DocumentRenderLog, GeneratedDocument
from .pdf import (
    CURRENT_TEMPLATE_VERSION,
    active_template_for,
    build_document_context,
    render_pdf,
)

logger = logging.getLogger(__name__)

# --- Document generation ---------------------------------------------------


def next_document_number():
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('document_number_seq')")
        value = cursor.fetchone()[0]
    return f"DOC-{value:06d}"


def document_type_for(transaction):
    """Which GeneratedDocument.document_type/DocumentTemplate this
    transaction renders as — a domain mapping, not a rendering concern, so
    it lives here rather than in pdf.py alongside the actual PDF assembly.
    """
    if transaction.movement_type == MovementType.ASSIGNMENT:
        return "assignment"
    if transaction.movement_type == MovementType.DISPOSAL:
        return "disposal"
    return "delivery"


_PRINTABLE_MOVEMENT_TYPES = (MovementType.ASSIGNMENT, MovementType.DELIVERY, MovementType.DISPOSAL)


def _transaction_country(txn):
    """The Country-level branding scope for this transaction — apps.documents.
    branding.CountryBrandingProfile. Assignment/delivery lines always carry
    their own `from_location` (set at the ledger write path); the
    transaction header's source_location/destination_location fields are
    only populated by some other movement types, so the per-line value
    (the same field build_document_context() already reads to list
    "source_locations" on the document itself) is the reliable source here,
    with the header fields only as a defensive fallback.
    """
    first_line = (
        txn.lines.filter(stock_reservation=None, from_location__isnull=False)
        .select_related("from_location")
        .order_by("line_number")
        .first()
    )
    if first_line:
        location = first_line.from_location
    else:
        location = txn.source_location or txn.destination_location
    return country_for_location(location)


def _record_render_log(
    *, document_type, template_obj, user, duration_ms, success, generated_document=None, error=""
):
    """Best-effort — a diagnostics row failing to save must never mask (or
    replace) the actual generation outcome above it. AppendOnlyModel means
    this is always a fresh INSERT, never a risk of clobbering another log.
    """
    try:
        DocumentRenderLog.objects.create(
            document_type=document_type,
            template=template_obj,
            template_version=(
                f"v{template_obj.version}" if template_obj else CURRENT_TEMPLATE_VERSION
            ),
            generated_document=generated_document,
            duration_ms=duration_ms,
            success=success,
            error_message=error[:4000],
            triggered_by=user,
        )
    except Exception:  # noqa: BLE001 - diagnostics logging must never break generation
        logger.exception("Could not record document render log")


def generate_document(*, txn, user, supersedes=None):
    """Renders and persists a PDF snapshot of a completed assignment/
    delivery/disposal transaction (spec §10, acceptance criterion §21.13,
    plus the disposal certificate — document_type_for() above).
    `supersedes`, when given, links to the GeneratedDocument this one
    replaces — regeneration never edits or removes the old row/file (doc 06).

    Every attempt that gets past the role/transaction-scope/movement-type
    checks below is logged to DocumentRenderLog (duration, template/version,
    success or the failure reason) for the Administrator-facing diagnostics
    page — an authorization rejection isn't a rendering failure, so those
    never produce a log row, only genuine render/storage/persistence
    outcomes do.
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    require_transaction_access(user, txn)

    if txn.movement_type not in _PRINTABLE_MOVEMENT_TYPES:
        raise ValidationError(
            "Only assignment, delivery, or disposal transactions can generate a "
            "printable document."
        )

    document_type = document_type_for(txn)
    started = time.monotonic()
    stored_file = None
    storage = None
    template_obj = None
    try:
        with transaction.atomic():
            document_number = next_document_number()
            template_obj = active_template_for(document_type)
            country = _transaction_country(txn)
            context = build_document_context(transaction=txn, document_number=document_number)
            try:
                pdf_bytes = render_pdf(
                    context,
                    document_type=document_type,
                    template_obj=template_obj,
                    country=country,
                )
            except ValidationError:
                raise
            except Exception as exc:
                # A custom document template (apps.documents.template_services.
                # update_template) is only validated against sample_document_
                # context() before it's saved — a real transaction's data can
                # still differ enough (e.g. an empty source_locations list, an
                # unusual movement_type_display) to make an otherwise-valid
                # template fail only now. Without this, that failure was an
                # uncaught WeasyPrint/TemplateSyntaxError exception — a raw
                # 500 on the transaction detail page — instead of a message
                # an Administrator can act on (fix or reset the template).
                raise ValidationError(f"Could not render the document template: {exc}") from exc

            document = GeneratedDocument(
                transaction=txn,
                document_number=document_number,
                document_type=document_type,
                template=template_obj,
                template_version=(
                    f"v{template_obj.version}" if template_obj else CURRENT_TEMPLATE_VERSION
                ),
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
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        if stored_file and storage:
            try:
                storage.delete(stored_file)
            except OSError:
                logger.exception("Could not clean up unsuccessful document file")
        if isinstance(exc, OSError):
            logger.exception("Could not store generated PDF")
            friendly = ValidationError(
                "The PDF could not be saved. Ask an Administrator to check document storage "
                "permissions and available disk space, then try again."
            )
            _record_render_log(
                document_type=document_type,
                template_obj=template_obj,
                user=user,
                duration_ms=duration_ms,
                success=False,
                error=str(exc),
            )
            raise friendly from exc
        _record_render_log(
            document_type=document_type,
            template_obj=template_obj,
            user=user,
            duration_ms=duration_ms,
            success=False,
            error=str(exc),
        )
        raise
    else:
        duration_ms = int((time.monotonic() - started) * 1000)
        _record_render_log(
            document_type=document_type,
            template_obj=template_obj,
            user=user,
            duration_ms=duration_ms,
            success=True,
            generated_document=document,
        )
        return document


def regenerate_document(*, previous_document, user):
    """Creates a new GeneratedDocument for the same transaction, linked back
    via `supersedes`. The previous document's row and PDF file are untouched.

    Deliberately not itself wrapped in @transaction.atomic — generate_document()
    already wraps its own writes in one internally, and nesting another atomic
    block here would turn its internal savepoint rollback (on a render/storage
    failure) into a rollback of *this* function's transaction too, silently
    discarding the DocumentRenderLog row generate_document() writes for that
    same failure right after.
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
