"""HTML->PDF rendering, isolated here (and in templates/documents/pdf/) so the
final company template can replace form_v1.html later without touching any
other code — docs/architecture/06-documents-and-snapshots.md.
"""

from django.template.loader import render_to_string
from weasyprint import HTML

from apps.inventory.models import MovementType

CURRENT_TEMPLATE_VERSION = "form_v1"


def build_document_context(*, transaction, document_number):
    """The exact data rendered into the PDF — also stored verbatim as
    GeneratedDocument.context_snapshot, so it's never re-derived from live
    Product/UnitAsset data after generation (doc 06).
    """
    lines = list(transaction.lines.select_related("unit_asset").order_by("line_number"))

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


def document_type_for(transaction):
    return "assignment" if transaction.movement_type == MovementType.ASSIGNMENT else "delivery"


def render_pdf(context):
    html_string = render_to_string(f"documents/pdf/{CURRENT_TEMPLATE_VERSION}.html", context)
    return HTML(string=html_string).write_pdf()
