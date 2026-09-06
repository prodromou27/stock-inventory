import os

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse

from apps.core.models import (
    AppendOnlyModel,
    AppendOnlyQuerySet,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


def _document_upload_path(instance, filename):
    # Storage name is derived from the row's own id, never the caller-supplied
    # filename — docs/architecture/06-documents-and-snapshots.md.
    return f"documents/{instance.id}.pdf"


def _attachment_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"attachments/{instance.id}{ext}"


def _template_logo_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"document_template_logos/{instance.id}{ext}"


class DocumentType(models.TextChoices):
    ASSIGNMENT = "assignment", "Assignment"
    DELIVERY = "delivery", "Delivery"
    DISPOSAL = "disposal", "Disposal certificate"


class LogoPosition(models.TextChoices):
    LEFT = "left", "Left"
    CENTER = "center", "Center"
    RIGHT = "right", "Right"


class FontChoice(models.TextChoices):
    SANS = "sans", "Sans-serif"
    SERIF = "serif", "Serif"
    MONO = "mono", "Monospace"


class PageMargin(models.TextChoices):
    COMPACT = "compact", "Compact"
    NORMAL = "normal", "Normal"
    SPACIOUS = "spacious", "Spacious"


class PageSize(models.TextChoices):
    A4 = "A4", "A4"
    LETTER = "Letter", "Letter"


class PageOrientation(models.TextChoices):
    PORTRAIT = "portrait", "Portrait"
    LANDSCAPE = "landscape", "Landscape"


class SectionSpacing(models.TextChoices):
    COMPACT = "compact", "Compact"
    NORMAL = "normal", "Normal"
    SPACIOUS = "spacious", "Spacious"


class TemplateStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"


# The fixed line-item column set every packaged/styleable PDF skeleton
# renders (apps.documents.pdf.build_document_context()'s "lines" shape) —
# DocumentTemplate.layout_config's "hidden_columns"/"column_order"/
# "column_labels" may only ever name one of these keys (apps.documents.
# template_services._clean_layout_config), never an arbitrary computed
# column: rendering stays bounded and safe by construction, the same trust
# model doc 06 already established for html_source itself.
REPORT_COLUMNS = [
    ("brand", "Brand"),
    ("model", "Model"),
    ("sku", "SKU"),
    ("type", "Type / Description"),
    ("serial", "Serial"),
    ("quantity", "Qty"),
    ("condition", "Condition"),
    ("accessories", "Accessories"),
]

# Single source of truth for every DocumentTemplate.layout_config key and its
# default — apps.documents.pdf.layout_context(), apps.documents.
# template_services.render_preview_pdf(), and apps.documents.forms.
# DocumentTemplateStyleForm.layout_config() all iterate this instead of each
# hand-listing the same keys (previously duplicated across all three plus
# this module — a maintainability gap flagged during this review: adding a
# key meant remembering to touch four separate places).
LAYOUT_CONFIG_DEFAULTS = {
    "page_size": PageSize.A4,
    "orientation": PageOrientation.PORTRAIT,
    "header_text": "",
    "footer_text": "",
    "show_page_numbers": False,
    "show_signature_block": True,
    "notes_text": "",
    "terms_text": "",
    "hidden_columns": [],
    "column_order": [],
    "column_labels": {},
}


def _default_layout_config():
    return dict(LAYOUT_CONFIG_DEFAULTS)


class GeneratedDocument(UUIDPrimaryKeyModel, AppendOnlyModel):
    """A PDF snapshot of a completed assignment/delivery transaction. Never
    updated in place — "regenerate" creates a new row with a fresh
    document_number (doc 02/06); the old PDF file and row are untouched.
    """

    transaction = models.ForeignKey(
        "inventory.InventoryTransaction",
        on_delete=models.PROTECT,
        related_name="generated_documents",
    )
    document_number = models.CharField(max_length=20, unique=True, editable=False)
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    template = models.ForeignKey(
        "DocumentTemplate",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_documents",
        help_text="Which DocumentTemplate row (if any — the packaged default has none) actually "
        "rendered this PDF. SET_NULL, not PROTECT: a later reset/reconfigure of that template "
        "must never be blocked by history that already has its own frozen context_snapshot and "
        "pdf_file — this FK is a convenience pointer, not the source of truth for what was "
        "printed.",
    )
    template_version = models.CharField(
        max_length=40,
        help_text="A human-readable snapshot of the template's version at generation time — "
        "'form_v1' for the packaged default, or 'v<N>' from DocumentTemplate.version when an "
        "Administrator-configured template rendered this document.",
    )
    context_snapshot = models.JSONField()
    pdf_file = models.FileField(upload_to=_document_upload_path)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="generated_documents"
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by"
    )

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["transaction"], name="gendoc_transaction_idx"),
        ]

    def __str__(self):
        return self.document_number

    def get_absolute_url(self):
        return reverse("documents:document_detail", kwargs={"pk": self.pk})


class Attachment(UUIDPrimaryKeyModel):
    """A scanned signed form (or other file) linked to a transaction. Not
    append-only — `is_deleted` is a legitimate later mutation — but every
    other field is set once at upload and never changed; a re-upload is
    always a new row (doc 02/06 — "never overwrite an existing attachment
    silently").
    """

    transaction = models.ForeignKey(
        "inventory.InventoryTransaction", on_delete=models.PROTECT, related_name="attachments"
    )
    file = models.FileField(upload_to=_attachment_upload_path)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_attachments"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["transaction"], name="attachment_transaction_idx"),
        ]

    def __str__(self):
        return self.original_filename

    def get_absolute_url(self):
        return reverse("documents:attachment_download", kwargs={"pk": self.pk})


class DocumentTemplate(UUIDPrimaryKeyModel, TimestampedModel):
    """Administrator-editable override of the packaged PDF form template
    (templates/documents/pdf/form_v1.html), one per DocumentType — lets an
    Administrator customize layout, wording, and branding (a logo) without a
    code deployment. `pdf.py`'s render_pdf() falls back to the packaged file
    template whenever no row exists for a given document_type, so this is
    purely additive — nothing breaks if no Administrator ever touches it.

    Not append-only: unlike GeneratedDocument (an immutable snapshot of what
    was actually printed), this is live configuration that's explicitly
    meant to be edited. It's also never hard-deleted, though: `is_active`
    is the "soft delete" apps.documents.template_services.reset_template()
    uses instead of DocumentTemplate.delete() — a deactivated row is hidden
    from every UI list and from active_template_for()'s real-generation
    lookup, but keeps existing GeneratedDocument.template FK references
    resolvable forever (spec: "templates referenced by history may be
    deactivated but not deleted").

    `status` (Draft/Published) is the second, independent gate:
    apps.documents.pdf.active_template_for() (real PDF generation) only ever
    considers a Published row, while apps.documents.template_services.
    get_template() (the editor's "what am I currently working on" lookup)
    returns the active row regardless of status — so a brand-new or
    in-progress edit never goes live until an Administrator explicitly
    publishes it, but can still be freely previewed.

    `html_source` is never typed by an Administrator directly — the editor
    (apps.documents.views.DocumentTemplateEditView) only exposes structured
    fields (logo/logo_position/accent_color/font_choice/page_margin/...);
    apps.documents.pdf.render_styleable_source() composes html_source from
    those against the packaged styleable_base.html skeleton, so the actual
    data fields (document_number, lines, signatures, ...) stay exactly
    where the packaged template puts them — never hand-placed. html_source
    itself remains the one thing pdf.py's render_pdf() reads, so nothing
    about PDF rendering or GeneratedDocument snapshotting changes.
    """

    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    is_active = models.BooleanField(
        default=True,
        help_text="False once reset/replaced — hidden everywhere but still resolvable by any "
        "GeneratedDocument that already references it.",
    )
    status = models.CharField(
        max_length=10, choices=TemplateStatus.choices, default=TemplateStatus.DRAFT
    )
    html_source = models.TextField()
    logo = models.FileField(upload_to=_template_logo_upload_path, null=True, blank=True)
    logo_position = models.CharField(
        max_length=10, choices=LogoPosition.choices, default=LogoPosition.LEFT
    )
    accent_color = models.CharField(max_length=7, default="#444444")
    font_choice = models.CharField(
        max_length=10, choices=FontChoice.choices, default=FontChoice.SANS
    )
    page_margin = models.CharField(
        max_length=10, choices=PageMargin.choices, default=PageMargin.NORMAL
    )
    section_spacing = models.CharField(
        max_length=10, choices=SectionSpacing.choices, default=SectionSpacing.NORMAL
    )
    heading_text_color = models.CharField(
        max_length=7,
        blank=True,
        help_text="#rrggbb, or blank to reuse the accent color (matches today's behavior).",
    )
    table_header_bg_color = models.CharField(
        max_length=7,
        blank=True,
        help_text="#rrggbb, or blank to keep the packaged default light-grey header row.",
    )
    document_title = models.CharField(
        max_length=120,
        blank=True,
        help_text="Overrides the auto-derived movement-type heading (e.g. 'Customer delivery') "
        "when set. Plain text — escaped like any other template variable, never HTML.",
    )
    company_name = models.CharField(max_length=200, blank=True)
    company_address = models.TextField(blank=True)
    company_tax_id = models.CharField(max_length=60, blank=True)
    logo_intentionally_omitted = models.BooleanField(
        default=False,
        help_text="Acknowledges this template has no logo on purpose — lets the completeness "
        "checklist pass without one, instead of treating a blank logo as an oversight forever.",
    )
    preview_confirmed = models.BooleanField(
        default=False,
        help_text="Set only by explicitly ticking the editor's confirmation checkbox on a save; "
        "any subsequent save resets it — an Administrator must re-review the PDF preview after "
        "every change before Publish is allowed.",
    )
    layout_config = models.JSONField(default=_default_layout_config)
    version = models.PositiveIntegerField(
        default=1,
        help_text="Bumped on every save (apps.documents.template_services.update_template) — "
        "the value snapshotted onto GeneratedDocument.template_version at generation time, and "
        "matching the DocumentTemplateVersion row created alongside it, so a historical document "
        "can always name exactly which configuration produced it.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document_type"],
                condition=Q(is_active=True),
                name="one_active_template_per_type",
            )
        ]

    def __str__(self):
        return f"{self.get_document_type_display()} template"


class DocumentTemplateVersion(UUIDPrimaryKeyModel, AppendOnlyModel):
    """One immutable snapshot per DocumentTemplate save — "restore" (apps.
    documents.template_services.restore_template_version()) copies an old
    snapshot's fields into a *new* save on the live row, never edits history,
    matching this app's ledger-style append-only pattern elsewhere
    (GeneratedDocument, AuditEvent, InventoryTransactionLine).

    Deliberately doesn't snapshot the logo file itself (just whether one was
    set) — restoring an old version restores text/style/layout fields, but
    never silently reintroduces a since-removed branding asset from binary
    storage; an Administrator re-uploads a logo explicitly if they want one
    back, the same as any other edit.
    """

    template = models.ForeignKey(
        DocumentTemplate, on_delete=models.CASCADE, related_name="versions"
    )
    version = models.PositiveIntegerField()
    field_snapshot = models.JSONField(
        help_text="html_source, style fields, document_title/company fields, and layout_config "
        "at the moment this version was saved — enough to fully restore the configuration."
    )
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(fields=["template", "version"], name="unique_template_version")
        ]

    def __str__(self):
        return f"{self.template} v{self.version}"


class DocumentRenderLog(UUIDPrimaryKeyModel, AppendOnlyModel):
    """One row per real document-generation attempt (apps.documents.services.
    generate_document()) — duration, which template/version rendered it, and
    whether it succeeded, purely for an Administrator-facing diagnostics view
    (spec: "record render duration, template version, failures, and storage
    errors"). Append-only: a historical health record, not something a later
    event should ever revise. Deliberately excludes preview renders (the
    editor's own Preview/live-preview buttons) — those are expected to fail
    often while an Administrator is actively drafting, and would drown out
    the signal real-generation failures are meant to surface.
    """

    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    template = models.ForeignKey(
        DocumentTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="render_logs",
        help_text="Null means the packaged default rendered it (no override existed at the time).",
    )
    template_version = models.CharField(max_length=40, blank=True)
    generated_document = models.ForeignKey(
        GeneratedDocument,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="render_log",
        help_text="Null when the render failed outright — never a document to point to.",
    )
    duration_ms = models.PositiveIntegerField()
    success = models.BooleanField()
    error_message = models.TextField(blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["success", "created_at"], name="renderlog_success_created_idx"),
        ]

    def __str__(self):
        status = "OK" if self.success else "FAILED"
        return f"{self.get_document_type_display()} render {status} ({self.duration_ms}ms)"


class DocumentIntegrityCheckRun(UUIDPrimaryKeyModel, AppendOnlyModel):
    """One row per run of the `check_document_integrity` management command
    (apps.documents.integrity) — a scheduled (host cron, same pattern as
    the daily notification digest and nightly export) walk of every
    GeneratedDocument confirming its pdf_file still exists and is a
    non-empty, real PDF in storage. Surfaces a missing/corrupt file to an
    Administrator via the diagnostics page before a user discovers it by
    clicking Download and getting a 404.
    """

    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    checked_count = models.PositiveIntegerField()
    missing_document_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="GeneratedDocument ids whose pdf_file was missing, empty, or not a valid PDF.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    @property
    def missing_count(self):
        return len(self.missing_document_ids)

    def __str__(self):
        return f"Integrity check {self.created_at:%Y-%m-%d} — {self.missing_count} missing"
