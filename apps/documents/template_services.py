from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, require_role

from .models import REPORT_COLUMNS, DocumentTemplate
from .pdf import (
    _default_layout_config,
    build_logo_data_uri,
    file_to_data_uri,
    render_pdf_from_source,
    sample_document_context,
    sniff_logo_content_type,
    visible_report_columns,
)

MAX_LOGO_SIZE_BYTES = 2 * 1024 * 1024


def get_template(document_type):
    return DocumentTemplate.objects.filter(document_type=document_type).first()


def _validate_template_renders(html_source):
    """Renders the submitted source against sample data before it's ever
    saved — a broken template must fail loudly on the settings screen, not
    silently the next time a Stock Manager tries to print a real document.
    """
    try:
        render_pdf_from_source(html_source, sample_document_context())
    except Exception as exc:  # noqa: BLE001 - any render failure becomes a clear form error
        raise ValidationError(f"Template failed to render: {exc}") from exc


def _validate_logo(logo_file):
    if logo_file.size > MAX_LOGO_SIZE_BYTES:
        raise ValidationError("Logo file exceeds the 2 MB size limit.")
    if sniff_logo_content_type(logo_file) is None:
        raise ValidationError("Logo must be a PNG or JPEG image.")


_VALID_REPORT_COLUMN_KEYS = {key for key, _ in REPORT_COLUMNS}


def _clean_layout_config(layout_config):
    """Hard allow-list on hidden_columns, same "never an arbitrary computed
    value" rule apps.documents.models.REPORT_COLUMNS documents — an unknown
    key is dropped, never stored or interpolated anywhere.
    """
    hidden = [
        key
        for key in (layout_config.get("hidden_columns") or [])
        if key in _VALID_REPORT_COLUMN_KEYS
    ]
    return {**layout_config, "hidden_columns": hidden}


@transaction.atomic
def update_template(
    *,
    user,
    document_type,
    html_source,
    logo=None,
    remove_logo=False,
    logo_position=None,
    accent_color=None,
    font_choice=None,
    page_margin=None,
    layout_config=None,
):
    """`html_source` is always the final, already-composed template — the
    structured editor (apps.documents.views.DocumentTemplateEditView) builds
    it via apps.documents.pdf.render_styleable_source() before calling this.
    logo_position/accent_color/font_choice/page_margin are optional and
    purely so the editor can show the Administrator's previous choices back
    on the next GET — omitting them (older/direct callers, e.g. this
    module's own tests) leaves those fields at their model defaults or
    whatever was already saved, without affecting html_source itself.
    """
    require_role(user, ADMINISTRATOR)

    _validate_template_renders(html_source)
    if logo is not None:
        _validate_logo(logo)

    template_obj = DocumentTemplate.objects.filter(document_type=document_type).first()
    is_new = template_obj is None
    old_html = template_obj.html_source if template_obj else None

    if is_new:
        template_obj = DocumentTemplate(document_type=document_type)

    template_obj.html_source = html_source
    template_obj.updated_by = user
    if logo_position is not None:
        template_obj.logo_position = logo_position
    if accent_color is not None:
        template_obj.accent_color = accent_color
    if font_choice is not None:
        template_obj.font_choice = font_choice
    if page_margin is not None:
        template_obj.page_margin = page_margin
    if logo is not None:
        template_obj.logo = logo
    elif remove_logo and template_obj.logo:
        template_obj.logo.delete(save=False)
        template_obj.logo = None
    if layout_config is not None:
        template_obj.layout_config = _clean_layout_config(layout_config)
    template_obj.version = 1 if is_new else template_obj.version + 1
    template_obj.full_clean()
    template_obj.save()

    record_event(
        actor=user,
        event_type=(
            AuditEvent.EventType.RECORD_CREATED if is_new else AuditEvent.EventType.RECORD_UPDATED
        ),
        obj=template_obj,
        summary=(
            f"{'Created' if is_new else 'Updated'} "
            f"{template_obj.get_document_type_display()} document template"
        ),
        old_values={"html_source": old_html} if old_html is not None else None,
        new_values={"html_source": html_source},
    )
    return template_obj


@transaction.atomic
def reset_template(*, user, document_type):
    require_role(user, ADMINISTRATOR)
    template_obj = DocumentTemplate.objects.filter(document_type=document_type).first()
    if template_obj is None:
        return

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=template_obj,
        summary=(
            f"Reset {template_obj.get_document_type_display()} "
            "document template to the packaged default"
        ),
    )
    if template_obj.logo:
        template_obj.logo.delete(save=False)
    template_obj.delete()


def render_preview_pdf(*, document_type, html_source, logo_file=None, layout_config=None):
    """Used by the settings screen's Preview button — renders the
    in-progress (not-yet-saved) template text against sample data. A newly
    chosen logo file takes precedence for this preview only; otherwise the
    already-saved logo (if any) is shown, so previewing doesn't require
    re-uploading the logo on every attempt. `layout_config`, likewise, is
    whatever the Administrator currently has typed in the form — previewed
    before it's saved, exactly like the logo.

    Always raises ValidationError on any rendering failure (never a raw
    TemplateSyntaxError/WeasyPrint exception) — the whole point of this
    function is to let an Administrator try out a possibly-broken template,
    so the caller (the preview view) needs one exception type to turn into a
    clean 400, not a 500.
    """
    context = dict(sample_document_context())
    if logo_file is not None:
        _validate_logo(logo_file)
        context["logo_data_uri"] = file_to_data_uri(logo_file)
    else:
        context["logo_data_uri"] = build_logo_data_uri(get_template(document_type))

    merged_layout = {**_default_layout_config(), **_clean_layout_config(layout_config or {})}
    context.update(
        page_size=merged_layout["page_size"],
        orientation=merged_layout["orientation"],
        header_text=merged_layout["header_text"],
        footer_text=merged_layout["footer_text"],
        show_page_numbers=merged_layout["show_page_numbers"],
        show_signature_block=merged_layout["show_signature_block"],
        notes_text=merged_layout["notes_text"],
        terms_text=merged_layout["terms_text"],
        report_columns=visible_report_columns(merged_layout["hidden_columns"]),
    )
    try:
        return render_pdf_from_source(html_source, context)
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 - any render failure becomes a clean form/HTTP error
        raise ValidationError(f"Template failed to render: {exc}") from exc
