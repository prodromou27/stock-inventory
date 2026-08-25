"""HTML->PDF rendering, isolated here (and in templates/documents/pdf/) so the
final company template can replace form_v1.html later without touching any
other code — docs/architecture/06-documents-and-snapshots.md.

An Administrator can additionally override the packaged template per
DocumentType at runtime (DocumentTemplate, no code deployment needed) — see
docs/architecture/06's "Editable document templates" section.
"""

import base64

from django.template import Context, Template, engines
from django.template.loader import render_to_string
from weasyprint import HTML

from apps.inventory.models import MovementType

CURRENT_TEMPLATE_VERSION = "form_v1"

_LOGO_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def build_document_context(*, transaction, document_number):
    """The exact data rendered into the PDF — also stored verbatim as
    GeneratedDocument.context_snapshot, so it's never re-derived from live
    Product/UnitAsset data after generation (doc 06). Every value here is a
    plain string/number/list — never a live model instance — so an
    Administrator-edited template (pdf.py's render_pdf()) can never reach
    back into the database through it.
    """
    lines = list(
        transaction.lines.filter(stock_reservation=None)
        .select_related("unit_asset")
        .order_by("line_number")
    )

    source_locations = sorted({str(line.from_location) for line in lines if line.from_location_id})

    return {
        "document_number": document_number,
        "transaction_number": transaction.transaction_number,
        "movement_type_display": transaction.get_movement_type_display(),
        "occurred_at": transaction.occurred_at.isoformat(),
        "employee_name": transaction.employee_name,
        "final_customer": transaction.final_customer,
        "project_reference": transaction.project_reference,
        "source_locations": source_locations,
        "notes": transaction.notes,
        "prepared_by": transaction.performed_by.get_username(),
        "lines": [
            {
                "line_number": line.line_number,
                "brand": line.brand_snapshot,
                "model": line.model_snapshot,
                "sku": line.sku_snapshot,
                "type": line.type_snapshot,
                "description": line.description_snapshot,
                "serial": line.serial_snapshot,
                "quantity": 1 if line.unit_asset_id else abs(line.quantity_delta),
                "condition": line.condition_snapshot,
                "accessories": line.accessories_snapshot,
            }
            for line in lines
        ],
    }


def sample_document_context():
    """Realistic placeholder data for previewing a template edit before any
    real transaction exists to render, and for validating a submitted
    template actually renders before it's saved (apps.documents.template_services).
    """
    return {
        "document_number": "DOC-000123",
        "transaction_number": "TXN-000456",
        "movement_type_display": "Customer delivery",
        "occurred_at": "2026-01-15",
        "employee_name": "",
        "final_customer": "Acme Corp",
        "project_reference": "PRJ-0001",
        "source_locations": ["Main Warehouse / Storage Room A"],
        "notes": "Sample preview data — no real transaction.",
        "prepared_by": "jdoe",
        "lines": [
            {
                "line_number": 1,
                "brand": "Cisco",
                "model": "C881",
                "sku": "",
                "type": "Router",
                "description": "",
                "serial": "SN-SAMPLE-001",
                "quantity": 1,
                "condition": "Good",
                "accessories": "Power adapter",
            },
            {
                "line_number": 2,
                "brand": "HP",
                "model": "26A",
                "sku": "CF226A",
                "type": "Toner",
                "description": "",
                "serial": "",
                "quantity": 3,
                "condition": "",
                "accessories": "",
            },
        ],
    }


def document_type_for(transaction):
    return "assignment" if transaction.movement_type == MovementType.ASSIGNMENT else "delivery"


def default_template_source():
    """The packaged file template's raw source — used as the Administrator
    editor's starting point (they edit a copy of what's already live, not a
    blank page) and as pdf.py's fallback whenever no DocumentTemplate row
    exists for a given type.
    """
    django_engine = engines["django"]
    template = django_engine.get_template(f"documents/pdf/{CURRENT_TEMPLATE_VERSION}.html")
    return template.template.source


def sniff_logo_content_type(file_obj):
    """Never trusts the client-supplied Content-Type — same magic-byte
    pattern as apps.documents.services._sniff_content_type.
    """
    file_obj.seek(0)
    header = file_obj.read(16)
    file_obj.seek(0)
    for signature, content_type in _LOGO_SIGNATURES:
        if header.startswith(signature):
            return content_type
    return None


def file_to_data_uri(file_obj):
    """A logo file (saved FieldFile or an in-memory UploadedFile) as an
    embeddable <img src="..."> data URI — WeasyPrint renders server-side
    from an HTML *string*, not a served page, so a data URI is the simplest
    way to embed an image regardless of storage backend or MEDIA_URL policy
    (media is never served directly — doc 06).
    """
    content_type = sniff_logo_content_type(file_obj)
    if content_type is None:
        return ""
    file_obj.seek(0)
    raw = file_obj.read()
    return f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"


def build_logo_data_uri(document_template):
    if document_template is None or not document_template.logo:
        return ""
    with document_template.logo.open("rb") as f:
        return file_to_data_uri(f)


def _active_template(document_type):
    from .models import DocumentTemplate

    return DocumentTemplate.objects.filter(document_type=document_type).first()


def render_pdf_from_source(html_source, context):
    """Renders arbitrary Django-template-syntax HTML (an Administrator's
    saved or in-progress override) against `context` and returns PDF bytes.
    Safe against template injection in the way that matters here: Django's
    template language has no arbitrary code execution (no function calls
    with arguments, no attribute access starting with "_"), and every value
    in `context` is always a plain string/number/list (build_document_context()),
    never a live model instance with callable methods.
    """
    html_string = Template(html_source).render(Context(context))
    return HTML(string=html_string).write_pdf()


def render_pdf(context, *, document_type):
    template_obj = _active_template(document_type)
    context = {**context, "logo_data_uri": build_logo_data_uri(template_obj)}
    if template_obj is not None:
        return render_pdf_from_source(template_obj.html_source, context)
    html_string = render_to_string(f"documents/pdf/{CURRENT_TEMPLATE_VERSION}.html", context)
    return HTML(string=html_string).write_pdf()
