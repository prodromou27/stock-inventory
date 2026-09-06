from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.template import Context, Template
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, require_role

from .layout import clean_presentation
from .models import REPORT_COLUMNS, DocumentTemplate, DocumentTemplateVersion, TemplateStatus
from .pdf import (
    _default_layout_config,
    build_logo_data_uri,
    file_to_data_uri,
    layout_context,
    render_pdf_from_source,
    sample_document_context,
    sniff_logo_content_type,
)

MAX_LOGO_SIZE_BYTES = 2 * 1024 * 1024

# Every plain (non-layout_config) field a version snapshot/restore/duplicate
# needs to copy — everything the structured editor exposes except the logo
# file itself (DocumentTemplateVersion's docstring explains why that's
# excluded) and the bookkeeping fields (is_active/status/version/updated_by)
# that a save/restore/duplicate always sets deliberately, not by copying.
_STYLE_FIELDS = (
    "logo_position",
    "accent_color",
    "font_choice",
    "page_margin",
    "section_spacing",
    "heading_text_color",
    "table_header_bg_color",
    "document_title",
    "company_name",
    "company_address",
    "company_tax_id",
    "logo_intentionally_omitted",
)


def get_template(document_type):
    """The current row for this document_type, regardless of Draft/Published
    status — what the editor screen works on. A Draft is still returned
    here (so an Administrator can keep editing/previewing it); only
    apps.documents.pdf.active_template_for() excludes it.
    """
    return DocumentTemplate.objects.filter(document_type=document_type, is_active=True).first()


def _validate_template_renders(html_source, template_obj=None):
    """Renders the submitted source against sample data before it's ever
    saved — a broken template must fail loudly on the settings screen, not
    silently the next time a Stock Manager tries to print a real document.
    Runs against two fixtures: the normal small sample, and a heavier one
    with many line items and long text fields, to catch gross overflow/
    exception cases up front. This is a best-effort proxy, not pixel-perfect
    overflow detection — WeasyPrint doesn't expose an overflow signal
    directly — but it does catch a template that outright breaks (an
    exception, not merely a visually-tight page) under realistic volume.
    """
    try:
        render_pdf_from_source(
            html_source,
            {
                **sample_document_context(),
                **layout_context(template_obj),
                "logo_data_uri": build_logo_data_uri(template_obj),
            },
        )
    except Exception as exc:  # noqa: BLE001 - any render failure becomes a clear form error
        raise ValidationError(f"Template failed to render: {exc}") from exc

    try:
        render_pdf_from_source(
            html_source,
            {
                **_stress_document_context(),
                **layout_context(template_obj),
                "logo_data_uri": build_logo_data_uri(template_obj),
            },
        )
    except Exception as exc:  # noqa: BLE001 - see above
        raise ValidationError(
            f"Template failed to render with a larger, more realistic document: {exc}"
        ) from exc


def _stress_document_context():
    """A heavier fixture than sample_document_context(): many line items and
    long text fields, so a template that renders fine with 2 tidy sample
    lines but breaks (raises) once a real delivery has 40 items or a long
    customer name is caught before publish, not after.
    """
    context = dict(sample_document_context())
    context["final_customer"] = "A Rather Long Customer Name For Stress-Testing Wrapping LLC"
    context["notes"] = "Stress-test note. " * 20
    context["lines"] = [
        {
            "line_number": i,
            "brand": f"Stress Brand {i}",
            "model": f"Model-{i:04d}-XL-VeryLongModelNumberForWrapping",
            "sku": f"SKU-{i:05d}",
            "type": "Stress Type",
            "description": (
                "A somewhat long description field to exercise wrapping." if i % 3 else ""
            ),
            "serial": f"SN-STRESS-{i:05d}",
            "quantity": (i % 5) + 1,
            "condition": "Used",
            "accessories": "Cable, adapter, manual" if i % 2 else "",
        }
        for i in range(1, 41)
    ]
    return context


def _validate_logo(logo_file):
    if logo_file.size > MAX_LOGO_SIZE_BYTES:
        raise ValidationError("Logo file exceeds the 2 MB size limit.")
    if sniff_logo_content_type(logo_file) is None:
        raise ValidationError("Logo must be a PNG or JPEG image.")


_VALID_REPORT_COLUMN_KEYS = {key for key, _ in REPORT_COLUMNS}


def _clean_layout_config(layout_config):
    """Hard allow-list on every layout_config key that names a report
    column — hidden_columns, column_order, and column_labels' keys — same
    "never an arbitrary computed value" rule apps.documents.models.
    REPORT_COLUMNS documents. Unknown column keys are dropped, never stored
    or interpolated anywhere; every other key round-trips as given (each
    already has its own type-appropriate validation at the form layer).
    """
    cleaned = {**layout_config}
    hidden = [
        key
        for key in (layout_config.get("hidden_columns") or [])
        if key in _VALID_REPORT_COLUMN_KEYS
    ]
    order = [
        key for key in (layout_config.get("column_order") or []) if key in _VALID_REPORT_COLUMN_KEYS
    ]
    labels = {
        key: value
        for key, value in (layout_config.get("column_labels") or {}).items()
        if key in _VALID_REPORT_COLUMN_KEYS
    }
    cleaned["hidden_columns"] = hidden
    cleaned["column_order"] = order
    cleaned["column_labels"] = labels
    cleaned.update(clean_presentation(layout_config))
    return cleaned


def _field_snapshot(template_obj):
    """Everything restore_template_version()/duplicate_template() need to
    reproduce a saved configuration — see DocumentTemplateVersion's
    docstring for why the logo file itself is deliberately excluded.
    """
    snapshot = {field: getattr(template_obj, field) for field in _STYLE_FIELDS}
    snapshot["html_source"] = template_obj.html_source
    snapshot["layout_config"] = template_obj.layout_config
    return snapshot


def _record_version(*, template_obj, user):
    DocumentTemplateVersion.objects.create(
        template=template_obj,
        version=template_obj.version,
        field_snapshot=_field_snapshot(template_obj),
        saved_by=user,
    )


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
    section_spacing=None,
    heading_text_color=None,
    table_header_bg_color=None,
    document_title=None,
    company_name=None,
    company_address=None,
    company_tax_id=None,
    logo_intentionally_omitted=None,
    preview_confirmed=False,
    layout_config=None,
):
    """`html_source` is always the final, already-composed template — the
    structured editor (apps.documents.views.DocumentTemplateEditView) builds
    it via apps.documents.pdf.render_styleable_source() before calling this.
    Every style/branding kwarg is optional and purely so the editor can show
    the Administrator's previous choices back on the next GET — omitting
    them (older/direct callers, e.g. this module's own tests) leaves those
    fields at their model defaults or whatever was already saved, without
    affecting html_source itself.

    A brand-new template (no existing active row for this document_type) is
    always created as Draft — see publish_template() for what makes it live.
    An existing row keeps whatever status it already had: an ordinary edit
    to an already-Published template takes effect immediately, exactly like
    before this feature existed; nothing here silently un-publishes it.
    Every successful save also records an immutable DocumentTemplateVersion
    snapshot (see that model's docstring) — never a silent overwrite of
    history.
    """
    require_role(user, ADMINISTRATOR)

    if logo is not None:
        _validate_logo(logo)

    template_obj = get_template(document_type)
    is_new = template_obj is None
    old_html = template_obj.html_source if template_obj else None
    old_logo = (
        template_obj.logo
        if template_obj and template_obj.logo and (logo is not None or remove_logo)
        else None
    )

    if is_new:
        template_obj = DocumentTemplate(document_type=document_type, status=TemplateStatus.DRAFT)

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
    if section_spacing is not None:
        template_obj.section_spacing = section_spacing
    if heading_text_color is not None:
        template_obj.heading_text_color = heading_text_color
    if table_header_bg_color is not None:
        template_obj.table_header_bg_color = table_header_bg_color
    if document_title is not None:
        template_obj.document_title = document_title
    if company_name is not None:
        template_obj.company_name = company_name
    if company_address is not None:
        template_obj.company_address = company_address
    if company_tax_id is not None:
        template_obj.company_tax_id = company_tax_id
    if logo_intentionally_omitted is not None:
        template_obj.logo_intentionally_omitted = logo_intentionally_omitted
    if logo is not None:
        template_obj.logo = logo
    elif remove_logo and template_obj.logo:
        template_obj.logo = None
    if layout_config is not None:
        template_obj.layout_config = _clean_layout_config(layout_config)
    # Any save resets this — an Administrator must re-review the PDF
    # preview after every change, not just once ever, before Publish is
    # allowed (see the model field's docstring and template_completeness()).
    template_obj.preview_confirmed = bool(preview_confirmed)
    if template_obj.status == TemplateStatus.PENDING_REVIEW:
        # The reviewed content just changed — that pending submission was
        # never actually reviewed in this form, so it must be resubmitted
        # (apps.documents.template_services.submit_for_review()) rather than
        # silently stay eligible for approve_and_publish() as-is.
        template_obj.status = TemplateStatus.DRAFT
        template_obj.submitted_by = None
        template_obj.submitted_at = None
    template_obj.version = 1 if is_new else template_obj.version + 1
    template_obj.full_clean()
    _validate_template_renders(html_source, template_obj)
    template_obj.save()
    if old_logo:
        old_logo.delete(save=False)
    _record_version(template_obj=template_obj, user=user)

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
def template_completeness(template_obj):
    """The pre-publish checklist (spec: "template completeness checklist
    before publishing: logo, title, company details, required sections,
    signatures, and PDF preview confirmation") — a list of
    {key, label, satisfied} dicts, most-important-first. publish_template()
    blocks on any unsatisfied item; the editor screen shows the same list
    live so an Administrator sees exactly what's missing before they even
    try to publish.
    """
    if template_obj is None:
        return []
    hidden_columns = set(template_obj.layout_config.get("hidden_columns") or [])
    visible_columns = {key for key, _ in REPORT_COLUMNS} - hidden_columns
    show_signatures = template_obj.layout_config.get("show_signature_block", True)
    return [
        {
            "key": "logo",
            "label": "Logo set, or explicitly marked not needed",
            "satisfied": bool(template_obj.logo) or template_obj.logo_intentionally_omitted,
        },
        {
            "key": "title",
            "label": "Document title set",
            "satisfied": bool(template_obj.document_title),
        },
        {
            "key": "company",
            "label": "Company name or address set",
            "satisfied": bool(template_obj.company_name or template_obj.company_address),
        },
        {
            "key": "columns",
            "label": "At least one line-item column is visible",
            "satisfied": bool(visible_columns),
        },
        {
            "key": "signatures",
            "label": "Signature block included",
            "satisfied": bool(show_signatures),
        },
        {
            "key": "preview",
            "label": "PDF preview reviewed and confirmed since the last change",
            "satisfied": template_obj.preview_confirmed,
        },
    ]


def _check_completeness_or_raise(template_obj):
    unmet = [item["label"] for item in template_completeness(template_obj) if not item["satisfied"]]
    if unmet:
        raise ValidationError(f"Cannot submit yet — incomplete: {'; '.join(unmet)}.")


@transaction.atomic
def submit_for_review(*, user, document_type):
    """Draft -> Pending review — the first half of the two-Administrator
    publish workflow (spec: "Administrator drafts; a second authorized
    Administrator reviews and publishes"). Requires template_completeness()
    to already be satisfied, same as the old single-step publish did — no
    point asking a second Administrator to review something with an
    already-known gap.
    """
    require_role(user, ADMINISTRATOR)
    template_obj = get_template(document_type)
    if template_obj is None:
        raise ValidationError("No template exists for this document type yet.")
    if template_obj.status == TemplateStatus.PUBLISHED:
        raise ValidationError("This template is already published.")
    if template_obj.status == TemplateStatus.PENDING_REVIEW:
        return template_obj

    _check_completeness_or_raise(template_obj)
    _validate_template_renders(template_obj.html_source, template_obj)

    template_obj.status = TemplateStatus.PENDING_REVIEW
    template_obj.submitted_by = user
    template_obj.submitted_at = timezone.now()
    template_obj.save(update_fields=["status", "submitted_by", "submitted_at", "updated_at"])
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=template_obj,
        summary=(
            f"Submitted {template_obj.get_document_type_display()} document template for review"
        ),
    )
    return template_obj


@transaction.atomic
def reject_review(*, user, document_type, reason=""):
    """Pending review -> Draft, without publishing — the reviewing
    Administrator sends it back (e.g. spotted something the completeness
    checklist can't catch, like a wording issue). The submitter re-edits
    and re-submits; update_template() already reverts to Draft on any edit,
    this just lets a reviewer do the same thing explicitly, with a reason,
    before any further edit happens.
    """
    require_role(user, ADMINISTRATOR)
    template_obj = get_template(document_type)
    if template_obj is None or template_obj.status != TemplateStatus.PENDING_REVIEW:
        raise ValidationError("This template is not currently pending review.")

    template_obj.status = TemplateStatus.DRAFT
    template_obj.submitted_by = None
    template_obj.submitted_at = None
    template_obj.save(update_fields=["status", "submitted_by", "submitted_at", "updated_at"])
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=template_obj,
        summary=(
            f"Sent {template_obj.get_document_type_display()} document template back to draft"
            + (f": {reason}" if reason else "")
        ),
    )
    return template_obj


@transaction.atomic
def publish_template(*, user, document_type):
    """Pending review -> Published — the second half of the two-
    Administrator workflow: makes the current active row's configuration
    live for real document generation (apps.documents.pdf.
    active_template_for()). Refuses when the approver is the same
    Administrator who submitted it (`user == template_obj.submitted_by`) —
    the whole point of a second reviewer is that it's actually a different
    person. Re-checks completeness and re-validates rendering defensively
    (update_template() already reverts any edited row back to Draft, so
    neither should ever actually fail here, but a submit-then-approve gap
    is exactly the kind of race this belongs to double-check rather than
    trust blindly).
    """
    require_role(user, ADMINISTRATOR)
    template_obj = get_template(document_type)
    if template_obj is None:
        raise ValidationError("No template exists for this document type yet.")
    if template_obj.status == TemplateStatus.PUBLISHED:
        return template_obj
    if template_obj.status == TemplateStatus.DRAFT:
        raise ValidationError("Submit this template for review before it can be published.")
    if template_obj.submitted_by_id == user.id:
        raise ValidationError(
            "A different Administrator must approve and publish — the same person can't "
            "review their own submission."
        )

    _check_completeness_or_raise(template_obj)
    _validate_template_renders(template_obj.html_source, template_obj)
    template_obj.status = TemplateStatus.PUBLISHED
    template_obj.approved_by = user
    template_obj.approved_at = timezone.now()
    # updated_at is auto_now=True — Django computes its new value regardless,
    # but a `save(update_fields=...)` call still only writes columns actually
    # named in that list, so it must be listed explicitly here or the row's
    # updated_at silently stays stale despite the in-memory value changing.
    template_obj.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=template_obj,
        summary=(
            f"Published {template_obj.get_document_type_display()} document template "
            f"(submitted by {template_obj.submitted_by})"
        ),
    )
    return template_obj


@transaction.atomic
def reset_template(*, user, document_type):
    """Deactivates (never deletes) the current active row — a
    GeneratedDocument that already references it keeps a resolvable
    `template` FK forever (spec: "templates referenced by history may be
    deactivated but not deleted"). get_template()/active_template_for()
    both filter on is_active, so a reset document_type immediately falls
    back to the packaged default, exactly as if no override existed.
    """
    require_role(user, ADMINISTRATOR)
    template_obj = get_template(document_type)
    if template_obj is None:
        return

    template_obj.is_active = False
    template_obj.save(update_fields=["is_active", "updated_at"])
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=template_obj,
        summary=(
            f"Reset {template_obj.get_document_type_display()} "
            "document template to the packaged default"
        ),
    )


@transaction.atomic
def duplicate_template(*, user, source_document_type, target_document_type):
    """Copies an existing template's full configuration into a new Draft row
    for a *different* document type — e.g. "start Assignment's template
    from Delivery's". Requires the target type to have no active row yet
    (only one active row per document_type, enforced by
    DocumentTemplate's own unique constraint): duplicating over an existing
    customization would silently discard it, so this asks for reset first
    instead. The logo, if any, is copied as a new file (never a shared
    reference — deleting/replacing one row's logo must never affect the
    other's).
    """
    require_role(user, ADMINISTRATOR)
    if source_document_type == target_document_type:
        raise ValidationError("Choose a different document type to duplicate into.")
    source = get_template(source_document_type)
    if source is None:
        raise ValidationError("The source document type has no template to duplicate.")
    if get_template(target_document_type) is not None:
        raise ValidationError(
            "The target document type already has a template — reset it first if you want to "
            "replace it with a duplicate."
        )

    new_template = DocumentTemplate(document_type=target_document_type, status=TemplateStatus.DRAFT)
    for field in _STYLE_FIELDS:
        setattr(new_template, field, getattr(source, field))
    new_template.html_source = source.html_source
    new_template.layout_config = dict(source.layout_config)
    new_template.updated_by = user
    new_template.version = 1
    if source.logo:
        source.logo.open("rb")
        try:
            new_template.logo.save(
                source.logo.name.rsplit("/", 1)[-1], ContentFile(source.logo.read()), save=False
            )
        finally:
            source.logo.close()
    new_template.full_clean()
    _validate_template_renders(new_template.html_source, new_template)
    new_template.save()
    _record_version(template_obj=new_template, user=user)

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_CREATED,
        obj=new_template,
        summary=(
            f"Duplicated {source.get_document_type_display()} document template into "
            f"{new_template.get_document_type_display()}"
        ),
    )
    return new_template


@transaction.atomic
def apply_starter_template(*, user, document_type, preset_key):
    """Creates a new Draft from one of apps.documents.gallery.STARTER_TEMPLATES
    — the same "brand-new Draft row, never touches an existing one" shape as
    duplicate_template(), just sourced from packaged data instead of another
    template. An Administrator can freely edit the result afterward; picking
    a starter is a one-time copy, not a live link back to the preset.
    """
    from .gallery import STARTER_TEMPLATES
    from .pdf import render_styleable_source

    require_role(user, ADMINISTRATOR)
    preset = STARTER_TEMPLATES.get(preset_key)
    if preset is None:
        raise ValidationError("Unknown starter template.")
    if get_template(document_type) is not None:
        raise ValidationError(
            "This document type already has a template — reset it first if you want to "
            "replace it with a starter layout."
        )

    fields = preset["fields"]
    html_source = render_styleable_source(
        logo_position=fields.get("logo_position", "left"),
        accent_color=fields.get("accent_color", "#444444"),
        font_choice=fields.get("font_choice", "sans"),
        page_margin=fields.get("page_margin", "normal"),
    )
    new_template = DocumentTemplate(document_type=document_type, status=TemplateStatus.DRAFT)
    for field in _STYLE_FIELDS:
        if field in fields:
            setattr(new_template, field, fields[field])
    new_template.html_source = html_source
    new_template.layout_config = _clean_layout_config({})
    new_template.updated_by = user
    new_template.version = 1
    new_template.full_clean()
    _validate_template_renders(new_template.html_source, new_template)
    new_template.save()
    _record_version(template_obj=new_template, user=user)

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_CREATED,
        obj=new_template,
        summary=(
            f"Started {new_template.get_document_type_display()} document template from the "
            f"'{preset['label']}' starter layout"
        ),
    )
    return new_template


@transaction.atomic
def restore_template_version(*, user, version_obj):
    """Copies an old DocumentTemplateVersion's fields into a *new* save on
    the live row for its document_type — never edits DocumentTemplateVersion
    history itself (append-only). If the document_type currently has no
    active row (e.g. reset since this version was saved), restoring
    recreates one, as a Draft.
    """
    require_role(user, ADMINISTRATOR)
    document_type = version_obj.template.document_type
    template_obj = get_template(document_type)
    is_new = template_obj is None
    if is_new:
        template_obj = DocumentTemplate(document_type=document_type, status=TemplateStatus.DRAFT)

    snapshot = version_obj.field_snapshot
    for field in _STYLE_FIELDS:
        if field in snapshot:
            setattr(template_obj, field, snapshot[field])
    template_obj.html_source = snapshot["html_source"]
    template_obj.layout_config = _clean_layout_config(snapshot.get("layout_config") or {})
    template_obj.updated_by = user
    template_obj.version = 1 if is_new else template_obj.version + 1
    template_obj.full_clean()
    _validate_template_renders(template_obj.html_source, template_obj)
    template_obj.save()
    _record_version(template_obj=template_obj, user=user)

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=template_obj,
        summary=(
            f"Restored {template_obj.get_document_type_display()} document template to v"
            f"{version_obj.version}"
        ),
    )
    return template_obj


def render_preview_pdf(
    *,
    document_type,
    html_source,
    logo_file=None,
    remove_logo=False,
    layout_config=None,
    output_format="pdf",
    **style,
):
    """Used by the settings screen's Preview button — renders the
    in-progress (not-yet-saved) template text against sample data. A newly
    chosen logo file takes precedence for this preview only; otherwise the
    already-saved logo (if any) is shown, so previewing doesn't require
    re-uploading the logo on every attempt. `layout_config`/`style` (the
    plain style fields — section_spacing, heading_text_color, ...),
    likewise, are whatever the Administrator currently has typed in the
    form — previewed before it's saved, exactly like the logo.

    Always raises ValidationError on any rendering failure (never a raw
    TemplateSyntaxError/WeasyPrint exception) — the whole point of this
    function is to let an Administrator try out a possibly-broken template,
    so the caller (the preview view) needs one exception type to turn into a
    clean 400, not a 500.
    """
    saved_template = get_template(document_type)
    context = dict(sample_document_context())
    try:
        if logo_file is not None:
            _validate_logo(logo_file)
            context["logo_data_uri"] = file_to_data_uri(logo_file)
        elif not remove_logo:
            # Reads the already-saved logo from storage — kept inside this
            # try block (not before it) so a missing/unreadable file (a
            # storage-permission problem, or a file removed out from under
            # the app) becomes the same clean ValidationError as any other
            # render failure, matching this function's own documented
            # promise, rather than an uncaught OSError/500.
            context["logo_data_uri"] = build_logo_data_uri(saved_template)

        # A throwaway, unsaved DocumentTemplate carries the in-progress style
        # fields into layout_context() without persisting anything — the
        # same function real generation uses, so preview and real output can
        # never drift apart in how they compute heading/table colors, etc.
        preview_template = DocumentTemplate(
            document_type=document_type,
            layout_config={
                **_default_layout_config(),
                **_clean_layout_config(layout_config or {}),
            },
            **{field: style[field] for field in _STYLE_FIELDS if field in style},
        )
        context.update(layout_context(preview_template))
        if output_format == "html":
            return Template(html_source).render(Context(context))
        return render_pdf_from_source(html_source, context)
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 - any render failure becomes a clean form/HTTP error
        raise ValidationError(f"Template failed to render: {exc}") from exc
