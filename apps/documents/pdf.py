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
from weasyprint import HTML, URLFetcher

from .layout import SECTIONS, clean_presentation
from .models import (
    LAYOUT_CONFIG_DEFAULTS,
    REPORT_COLUMNS,
    FontChoice,
    PageMargin,
    SectionSpacing,
    TemplateStatus,
    _default_layout_config,
)

CURRENT_TEMPLATE_VERSION = "form_v1"
STYLEABLE_TEMPLATE_NAME = "documents/pdf/styleable_base.html"

# Fonts actually installed in the runtime image (deploy/Dockerfile's
# fonts-liberation package) — restricting the editor's choices to these
# means "Font" always renders as chosen, never silently substitutes.
_FONT_STACKS = {
    FontChoice.SANS: '"Liberation Sans", Arial, sans-serif',
    FontChoice.SERIF: '"Liberation Serif", "Times New Roman", serif',
    FontChoice.MONO: '"Liberation Mono", "Courier New", monospace',
}

_PAGE_MARGINS_CM = {
    PageMargin.COMPACT: "1.5",
    PageMargin.NORMAL: "2",
    PageMargin.SPACIOUS: "2.5",
}

_SECTION_SPACING_EM = {
    SectionSpacing.COMPACT: "0.5em",
    SectionSpacing.NORMAL: "1em",
    SectionSpacing.SPACIOUS: "2em",
}

_DEFAULT_HEADING_COLOR = "#444444"
_DEFAULT_TABLE_HEADER_BG = "#eeeeee"

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
        .select_related("unit_asset", "from_location")
        .order_by("line_number")
    )

    source_locations = sorted({str(line.from_location) for line in lines if line.from_location_id})

    return {
        "document_number": document_number,
        "document_type": transaction.movement_type,
        "transaction_number": transaction.transaction_number,
        "movement_type_display": transaction.get_movement_type_display(),
        "occurred_at": transaction.occurred_at.isoformat(),
        "document_date_display": f"{transaction.occurred_at.day} {transaction.occurred_at:%B %Y}",
        "employee_name": transaction.employee_name,
        "final_customer": transaction.final_customer,
        "project_reference": transaction.project_reference,
        "source_locations": source_locations,
        "wipe_method_display": (
            transaction.get_wipe_method_display() if transaction.wipe_method else ""
        ),
        "witness_name": transaction.witness_name,
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
        "document_type": "delivery",
        "transaction_number": "TXN-000456",
        "movement_type_display": "Customer delivery",
        "occurred_at": "2026-01-15",
        "document_date_display": "15 January 2026",
        "employee_name": "",
        "final_customer": "Acme Corp",
        "project_reference": "PRJ-0001",
        "source_locations": ["Main Warehouse / Storage Room A"],
        "wipe_method_display": "Software data wipe",
        "witness_name": "R. Patel",
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
                "condition": "Used",
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


def default_template_source():
    """The packaged file template's raw source — used as the Administrator
    editor's starting point (they edit a copy of what's already live, not a
    blank page) and as pdf.py's fallback whenever no DocumentTemplate row
    exists for a given type.
    """
    django_engine = engines["django"]
    template = django_engine.get_template(f"documents/pdf/{CURRENT_TEMPLATE_VERSION}.html")
    return template.template.source


def render_styleable_source(*, logo_position, accent_color, font_choice, page_margin):
    """Composes a DocumentTemplate.html_source string from the four choices
    an Administrator makes in the structured editor (apps.documents.views.
    DocumentTemplateEditView) — never hand-typed HTML. Starts from the
    packaged styleable_base.html skeleton (same data fields/layout as
    form_v1.html, the packaged default) and substitutes plain string tokens
    for the style choices — deliberately not Django template syntax, so this
    substitution can never collide with or interfere with the `{{ }}`/
    `{% %}` data-field tags the skeleton already contains for document_number,
    lines, signatures, etc. Those stay exactly where the skeleton puts them.

    accent_color is expected to already be a validated "#rrggbb" string
    (apps.documents.forms.DocumentTemplateStyleForm.clean_accent_color) —
    interpolated directly into the PDF's <style> block, so an unvalidated
    value here would be a CSS-injection path into WeasyPrint's renderer.
    """
    django_engine = engines["django"]
    source = django_engine.get_template(STYLEABLE_TEMPLATE_NAME).template.source
    # Freeze the section markup into the saved source/version, rather than
    # allowing later packaged partial edits to change an existing template.
    section_source = "{% for section_key in section_order %}"
    section_source += (
        "{% for block in custom_blocks %}{% if block.before == section_key %}"
        '<div class="custom-text-block" style="text-align:{{ block.alignment }};'
        'margin:1em 0;">{{ block.text|linebreaksbr }}</div>'
        "{% endif %}{% endfor %}"
    )
    for key, _ in SECTIONS:
        section_source += '{% if section_key == "' + key + '" %}'
        section_source += django_engine.get_template(
            f"documents/pdf/sections/{key}.html"
        ).template.source
        section_source += "{% endif %}"
    section_source += "{% endfor %}"
    section_placeholder = "\n".join(
        (
            "{% for section_template in document_sections %}",
            "    {% include section_template %}",
            "  {% endfor %}",
        )
    )
    source = source.replace(section_placeholder, section_source)
    return (
        source.replace("__FONT_STACK__", _FONT_STACKS[font_choice])
        .replace("__PAGE_MARGIN_CM__", _PAGE_MARGINS_CM[page_margin])
        .replace("__ACCENT_COLOR__", accent_color)
        .replace("__LOGO_POSITION_CLASS__", f"letterhead--{logo_position}")
    )


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
    if not document_template.logo._committed:
        return file_to_data_uri(document_template.logo.file)
    with document_template.logo.open("rb") as f:
        return file_to_data_uri(f)


def active_template_for(document_type):
    """The template actually used for a *real* generated document — a Draft
    is never picked up here, only Published (preview ignores persisted
    state entirely and reads straight from the in-progress form, so it
    already shows a Draft's changes without needing this function to).
    """
    from .models import DocumentTemplate

    return DocumentTemplate.objects.filter(
        document_type=document_type, is_active=True, status=TemplateStatus.PUBLISHED
    ).first()


# Rejects every URL except `data:` — WeasyPrint's default fetcher would
# otherwise make the *server* fetch whatever URL appears in a template's
# HTML/CSS (an <img src>, a CSS url(), an @font-face src), which becomes a
# real SSRF vector once an Administrator can type raw HTML directly (see
# DocumentTemplate.custom_html_enabled) rather than only choosing from the
# structured editor's fixed style options. The app already never needs
# this: a logo is always embedded as a data URI (build_logo_data_uri())
# specifically so WeasyPrint never has to fetch it from anywhere. Applied to
# every render, not just custom-HTML templates, as defense in depth. A
# disallowed URL becomes a ValueError, which WeasyPrint catches per-resource
# and simply omits (a missing image/font), never aborting the whole render.
_DATA_URI_ONLY_FETCHER = URLFetcher(allowed_protocols=["data"])


def _pdf_options():
    """Losslessly preserve text/vector content while reducing embedded image bloat."""
    from django.conf import settings

    return {
        "optimize_images": True,
        "jpeg_quality": settings.DOCUMENT_PDF_JPEG_QUALITY,
        "dpi": settings.DOCUMENT_PDF_IMAGE_DPI,
    }


def render_pdf_from_source(html_source, context):
    """Renders arbitrary Django-template-syntax HTML (an Administrator's
    saved or in-progress override) against `context` and returns PDF bytes.
    Safe against template injection in the way that matters here: Django's
    template language has no arbitrary code execution (no function calls
    with arguments, no attribute access starting with "_"), and every value
    in `context` is always a plain string/number/list (build_document_context()),
    never a live model instance with callable methods. External resource
    fetching is separately locked to data: URIs only — see
    _DATA_URI_ONLY_FETCHER above.
    """
    html_string = Template(html_source).render(Context(context))
    return HTML(string=html_string, url_fetcher=_DATA_URI_ONLY_FETCHER).write_pdf(**_pdf_options())


def sanitize_css_content_text(value):
    """Strips characters that could break out of the CSS `content: "..."`
    string header_text/footer_text are interpolated into (styleable_base.html's
    @page rule). Django's HTML autoescaping happens to neutralize a literal
    double-quote (-> &quot;, inert inside a CSS string) but leaves a backslash
    or an embedded newline untouched, either of which can produce broken or
    surprising CSS — never a script-execution risk, but real enough to close
    outright rather than rely on incidental escaping. Applied primarily at
    the form layer (apps.documents.forms) and again here, defensively, for
    any other caller of layout_context()/render_styleable_source().
    """
    if not value:
        return value
    return value.replace("\\", "").replace('"', "").replace("\n", " ").replace("\r", " ")


def visible_report_columns(hidden_columns, column_order=None, column_labels=None):
    """[(key, label), ...] from REPORT_COLUMNS, filtered/reordered/relabeled
    by an Administrator's layout_config — unrecognized keys are always
    silently ignored (never an arbitrary computed column; see
    REPORT_COLUMNS's docstring). `column_order` only ever reorders; it can't
    introduce a column hidden_columns removed. `column_labels` only ever
    renames a key that's already in REPORT_COLUMNS.
    """
    hidden = set(hidden_columns or [])
    columns = [(key, label) for key, label in REPORT_COLUMNS if key not in hidden]
    if column_order:
        valid_keys = {key for key, _ in REPORT_COLUMNS}
        order_index = {key: i for i, key in enumerate(column_order) if key in valid_keys}
        columns.sort(key=lambda pair: order_index.get(pair[0], len(order_index)))
    if column_labels:
        columns = [(key, column_labels.get(key) or label) for key, label in columns]
    return columns


def layout_context(template_obj):
    """The per-generation context a rendered PDF needs beyond the
    transaction data itself — DocumentTemplate.layout_config (spacing,
    header/footer, notes, page numbers, column layout) plus the template's
    own plain style/branding fields (section spacing, heading/table-header
    colors, document title, company info), always present with sane
    defaults so form_v1.html (used when no DocumentTemplate row exists at
    all) can reference any of these harmlessly even with template_obj=None.
    """
    config = {**_default_layout_config(), **(template_obj.layout_config if template_obj else {})}
    context = {key: config.get(key, default) for key, default in LAYOUT_CONFIG_DEFAULTS.items()}
    context.update(clean_presentation(config))
    context["document_sections"] = [
        f"documents/pdf/sections/{key}.html" for key in context["section_order"]
    ]
    context["header_text"] = sanitize_css_content_text(context["header_text"])
    context["footer_text"] = sanitize_css_content_text(context["footer_text"])
    context["report_columns"] = visible_report_columns(
        context.pop("hidden_columns"), context.pop("column_order"), context.pop("column_labels")
    )

    accent_color = template_obj.accent_color if template_obj else _DEFAULT_HEADING_COLOR
    heading_color = (template_obj.heading_text_color if template_obj else "") or accent_color
    table_header_bg = (
        template_obj.table_header_bg_color if template_obj else ""
    ) or _DEFAULT_TABLE_HEADER_BG
    section_spacing = template_obj.section_spacing if template_obj else SectionSpacing.NORMAL
    context.update(
        heading_text_color=heading_color,
        table_header_bg_color=table_header_bg,
        section_spacing_em=_SECTION_SPACING_EM.get(section_spacing, "1em"),
        document_title=template_obj.document_title if template_obj else "",
        company_name=template_obj.company_name if template_obj else "",
        company_address=template_obj.company_address if template_obj else "",
        company_tax_id=template_obj.company_tax_id if template_obj else "",
    )
    return context


def render_pdf(context, *, document_type, template_obj=None, country=None):
    if template_obj is None:
        template_obj = active_template_for(document_type)
    context = {
        **context,
        "logo_data_uri": build_logo_data_uri(template_obj),
        **layout_context(template_obj),
    }
    if country is not None:
        from .branding import apply_branding_override

        context = apply_branding_override(context, country=country)
    if template_obj is not None:
        return render_pdf_from_source(template_obj.html_source, context)
    html_string = render_to_string(f"documents/pdf/{CURRENT_TEMPLATE_VERSION}.html", context)
    return HTML(string=html_string, url_fetcher=_DATA_URI_ONLY_FETCHER).write_pdf(**_pdf_options())
