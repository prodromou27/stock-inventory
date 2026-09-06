import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseServerError,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, RoleRequiredMixin
from apps.inventory.access import require_transaction_access
from apps.inventory.models import InventoryTransaction
from apps.locations.models import Location, LocationLevel

from .branding import (
    eligible_countries,
    get_branding_profile,
    list_branding_profiles,
    save_branding_profile,
)
from .forms import AttachmentUploadForm, CountryBrandingForm, DocumentTemplateStyleForm
from .gallery import STARTER_TEMPLATES
from .layout import SECTIONS
from .models import (
    REPORT_COLUMNS,
    Attachment,
    DocumentIntegrityCheckRun,
    DocumentRenderLog,
    DocumentTemplateVersion,
    DocumentType,
    FontChoice,
    GeneratedDocument,
    LogoPosition,
    PageMargin,
)
from .pdf import render_pdf, render_styleable_source, sample_document_context
from .services import delete_attachment, generate_document, regenerate_document, upload_attachment
from .template_services import (
    apply_starter_template,
    duplicate_template,
    get_template,
    publish_template,
    reject_review,
    render_preview_pdf,
    reset_template,
    restore_template_version,
    submit_for_review,
    template_completeness,
    update_template,
)

logger = logging.getLogger(__name__)


class GenerateDocumentView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def post(self, request, pk):
        txn = get_object_or_404(InventoryTransaction, pk=pk)
        try:
            document = generate_document(txn=txn, user=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect(txn.get_absolute_url())

        messages.success(request, f"Generated document {document.document_number}.")
        return redirect(document.get_absolute_url())


class RegenerateDocumentView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def post(self, request, pk):
        previous = get_object_or_404(GeneratedDocument, pk=pk)
        require_transaction_access(request.user, previous.transaction)
        try:
            document = regenerate_document(previous_document=previous, user=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect(previous.get_absolute_url())

        messages.success(request, f"Regenerated as document {document.document_number}.")
        return redirect(document.get_absolute_url())


class DocumentDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        document = get_object_or_404(
            GeneratedDocument.objects.select_related("transaction", "generated_by", "supersedes"),
            pk=pk,
        )
        require_transaction_access(request.user, document.transaction)
        return render(request, "documents/document_detail.html", {"document": document})


class DocumentDownloadView(LoginRequiredMixin, View):
    """Streams the PDF directly — the file lives under MEDIA_ROOT, which is
    never wired into urls.py for direct serving, so this view is the only
    way to reach it, and it re-checks scope on every request (spec §11/§17).
    """

    def get(self, request, pk):
        document = get_object_or_404(GeneratedDocument.objects.select_related("transaction"), pk=pk)
        require_transaction_access(request.user, document.transaction)

        if not document.pdf_file:
            raise Http404("No PDF file stored for this document.")

        try:
            pdf_file = document.pdf_file.open("rb")
        except FileNotFoundError as exc:
            raise Http404(
                "The stored PDF is missing. An Administrator can restore it from backup."
            ) from exc
        return FileResponse(
            pdf_file,
            as_attachment=False,
            filename=f"{document.document_number}.pdf",
            content_type="application/pdf",
        )


class AttachmentUploadView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "documents/attachment_upload_form.html"

    def get(self, request, pk):
        txn = get_object_or_404(InventoryTransaction, pk=pk)
        require_transaction_access(request.user, txn)
        form = AttachmentUploadForm()
        return render(request, self.template_name, {"form": form, "transaction": txn})

    def post(self, request, pk):
        txn = get_object_or_404(InventoryTransaction, pk=pk)
        require_transaction_access(request.user, txn)
        form = AttachmentUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "transaction": txn})

        try:
            upload_attachment(txn=txn, uploaded_file=form.cleaned_data["file"], user=request.user)
        except ValidationError as exc:
            form.add_error("file", exc)
            return render(request, self.template_name, {"form": form, "transaction": txn})

        messages.success(request, "Attachment uploaded.")
        return redirect(txn.get_absolute_url())


class AttachmentDownloadView(LoginRequiredMixin, View):
    def get(self, request, pk):
        attachment = get_object_or_404(Attachment.objects.select_related("transaction"), pk=pk)
        require_transaction_access(request.user, attachment.transaction)
        if attachment.is_deleted:
            raise Http404("Attachment has been deleted.")

        return FileResponse(
            attachment.file.open("rb"),
            as_attachment=True,
            filename=attachment.original_filename,
            content_type=attachment.content_type,
        )


class AttachmentDeleteView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, pk):
        attachment = get_object_or_404(Attachment, pk=pk)
        try:
            delete_attachment(attachment=attachment, user=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Attachment deleted.")
        return redirect(attachment.transaction.get_absolute_url())


# --- Editable document templates ("from Settings", user request) ---------


class DocumentTemplateHubView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Administrator-only Edit/Reset; Stock Manager gets read-only Preview
    of whatever's currently live (the packaged default or an Administrator's
    saved override) — never an in-progress, unsaved edit. Both roles already
    generate real documents from these templates; this just lets a Stock
    Manager see the same rendering before relying on it.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def get(self, request):
        rows = [
            {
                "document_type": value,
                "label": label,
                "template_obj": get_template(value),
            }
            for value, label in DocumentType.choices
        ]
        return render(
            request,
            "documents/template_hub.html",
            {"rows": rows, "can_edit": request.user.groups.filter(name=ADMINISTRATOR).exists()},
        )


class DocumentTemplateLivePreviewView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Renders whatever is *actually currently live* for this document type
    (the packaged default, or an Administrator's saved override) against
    sample data — never live transaction data, never an unsaved in-progress
    edit (that's DocumentTemplatePreviewView, Administrator-only). Read-only
    by construction: there is nothing here for a Stock Manager to change.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def get(self, request, document_type):
        _require_valid_document_type(document_type)
        try:
            pdf_bytes = render_pdf(sample_document_context(), document_type=document_type)
        except ValidationError as exc:
            return HttpResponseBadRequest(
                "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            )
        except OSError:
            # The published template's logo file couldn't be read (missing
            # from storage, or a storage-permission problem — the same
            # class of failure apps.documents.services.generate_document()
            # already turns into a friendly message for real generation;
            # this is that same protection for the read-only preview path,
            # which previously had none at all and surfaced a raw 500).
            logger.exception("Could not render live preview for document_type=%s", document_type)
            return HttpResponseServerError(
                "This template's logo could not be read. Ask an Administrator to check document "
                "storage permissions, or remove and re-upload the logo, then try again."
            )
        return HttpResponse(pdf_bytes, content_type="application/pdf")


def _require_valid_document_type(document_type):
    if document_type not in DocumentType.values:
        raise Http404("Unknown document type.")


# Every DocumentTemplate plain style/branding field the structured editor
# exposes, beyond the four render_styleable_source() bakes into html_source
# — shared by DocumentTemplateEditView and DocumentTemplatePreviewView so
# neither can drift from the other about which fields exist.
_STYLE_KWARG_FIELDS = (
    "section_spacing",
    "heading_text_color",
    "table_header_bg_color",
    "document_title",
    "company_name",
    "company_address",
    "company_tax_id",
    "logo_intentionally_omitted",
    "custom_html_enabled",
)


def _style_kwargs(data):
    return {field: data[field] for field in _STYLE_KWARG_FIELDS}


class DocumentTemplateEditView(LoginRequiredMixin, RoleRequiredMixin, View):
    """A structured branding panel, not an HTML editor — logo, its position,
    colors, font, margins/spacing, and company/title text are the only
    things an Administrator chooses. The report's actual data fields are
    always placed automatically by the packaged skeleton (apps.documents.pdf.
    render_styleable_source()); nothing here is typed as template syntax.

    A brand-new template always saves as Draft (see the "Publish" action);
    an already-Published one keeps saving live on every edit, unchanged
    from this feature's pre-Draft/Published behavior.
    """

    allowed_roles = (ADMINISTRATOR,)
    template_name = "documents/template_edit.html"

    def get(self, request, document_type):
        _require_valid_document_type(document_type)
        template_obj = get_template(document_type)
        form = DocumentTemplateStyleForm(initial=self._initial(template_obj))
        return self._render(request, document_type, form, template_obj)

    def post(self, request, document_type):
        _require_valid_document_type(document_type)
        template_obj = get_template(document_type)
        form = DocumentTemplateStyleForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._render(request, document_type, form, template_obj)

        data = form.cleaned_data
        if data["custom_html_enabled"]:
            html_source = data["custom_html_source"]
        else:
            html_source = render_styleable_source(
                logo_position=data["logo_position"],
                accent_color=data["accent_color"],
                font_choice=data["font_choice"],
                page_margin=data["page_margin"],
            )
        try:
            update_template(
                user=request.user,
                document_type=document_type,
                html_source=html_source,
                logo=data.get("logo"),
                remove_logo=data.get("remove_logo", False),
                logo_position=data["logo_position"],
                accent_color=data["accent_color"],
                font_choice=data["font_choice"],
                page_margin=data["page_margin"],
                layout_config=form.layout_config(),
                preview_confirmed=data["preview_confirmed"],
                **_style_kwargs(data),
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
            return self._render(request, document_type, form, template_obj)

        messages.success(request, "Template saved.")
        return redirect("documents:template_edit", document_type=document_type)

    @staticmethod
    def _initial(template_obj):
        if template_obj is None:
            # Turning the raw-HTML toggle on for a brand-new template
            # starts from the same skeleton the structured composer would
            # have produced with its own defaults — a copy of something
            # real, not a blank page (default_template_source()'s own
            # philosophy, applied here to the styleable skeleton instead of
            # the packaged form_v1.html, since that's what a structured
            # save would otherwise have produced).
            return {
                "custom_html_source": render_styleable_source(
                    logo_position=LogoPosition.LEFT,
                    accent_color="#444444",
                    font_choice=FontChoice.SANS,
                    page_margin=PageMargin.NORMAL,
                )
            }
        initial = {
            "logo_position": template_obj.logo_position,
            "accent_color": template_obj.accent_color,
            "font_choice": template_obj.font_choice,
            "page_margin": template_obj.page_margin,
            **{field: getattr(template_obj, field) for field in _STYLE_KWARG_FIELDS},
            **template_obj.layout_config,
            # Always prefilled from the current source, on or off — turning
            # the toggle on starts from a copy of what's already live/
            # composed, not a blank page.
            "custom_html_source": template_obj.html_source,
        }
        initial["column_order"] = ",".join(template_obj.layout_config.get("column_order") or [])
        initial["section_order"] = ",".join(template_obj.layout_config.get("section_order") or [])
        initial["column_labels"] = "\n".join(
            f"{key}:{label}"
            for key, label in (template_obj.layout_config.get("column_labels") or {}).items()
        )
        return initial

    def _render(self, request, document_type, form, template_obj):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "document_type": document_type,
                "document_type_label": dict(DocumentType.choices)[document_type],
                "template_obj": template_obj,
                "editor_columns": REPORT_COLUMNS,
                "editor_sections": SECTIONS,
                "other_document_types": [
                    (value, label)
                    for value, label in DocumentType.choices
                    if value != document_type
                ],
                "versions": (
                    template_obj.versions.select_related("saved_by")[:20] if template_obj else []
                ),
                "completeness": template_completeness(template_obj),
                "starter_templates": STARTER_TEMPLATES,
            },
        )


class DocumentTemplatePreviewView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, document_type):
        _require_valid_document_type(document_type)
        form = DocumentTemplateStyleForm(request.POST, request.FILES)
        if not form.is_valid():
            return HttpResponseBadRequest(
                "; ".join(f"{field}: {' '.join(errs)}" for field, errs in form.errors.items())
            )

        data = form.cleaned_data
        if data["custom_html_enabled"]:
            html_source = data["custom_html_source"]
        else:
            html_source = render_styleable_source(
                logo_position=data["logo_position"],
                accent_color=data["accent_color"],
                font_choice=data["font_choice"],
                page_margin=data["page_margin"],
            )
        try:
            pdf_bytes = render_preview_pdf(
                document_type=document_type,
                html_source=html_source,
                logo_file=data.get("logo"),
                remove_logo=data.get("remove_logo", False),
                output_format="html" if request.GET.get("format") == "html" else "pdf",
                layout_config=form.layout_config(),
                accent_color=data["accent_color"],
                **_style_kwargs(data),
            )
        except ValidationError as exc:
            return HttpResponseBadRequest(
                "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            )
        return HttpResponse(
            pdf_bytes,
            content_type="text/html" if request.GET.get("format") == "html" else "application/pdf",
        )


class DocumentTemplateResetView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, document_type):
        _require_valid_document_type(document_type)
        reset_template(user=request.user, document_type=document_type)
        messages.success(request, "Template reset to the packaged default.")
        return redirect("documents:template_edit", document_type=document_type)


class DocumentTemplateSubmitForReviewView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Draft -> Pending review — the first half of the two-Administrator
    publish workflow.
    """

    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, document_type):
        _require_valid_document_type(document_type)
        try:
            submit_for_review(user=request.user, document_type=document_type)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(
                request, "Submitted for review — a different Administrator must now approve it."
            )
        return redirect("documents:template_edit", document_type=document_type)


class DocumentTemplateRejectReviewView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Pending review -> Draft, without publishing."""

    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, document_type):
        _require_valid_document_type(document_type)
        reason = request.POST.get("reason", "")
        try:
            reject_review(user=request.user, document_type=document_type, reason=reason)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Sent back to draft.")
        return redirect("documents:template_edit", document_type=document_type)


class DocumentTemplatePublishView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Pending review -> Published, by a *different* Administrator from
    whoever submitted it — makes the configuration live for real document
    generation. A no-op (still succeeds) if it's already Published.
    """

    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, document_type):
        _require_valid_document_type(document_type)
        try:
            publish_template(user=request.user, document_type=document_type)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Template published.")
        return redirect("documents:template_edit", document_type=document_type)


class DocumentTemplateDuplicateView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Copies `document_type`'s current template into a different document
    type's brand-new Draft — see duplicate_template()'s docstring for why
    same-type duplication (an alternate draft alongside a live published
    one) isn't offered in this first increment.
    """

    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, document_type):
        _require_valid_document_type(document_type)
        target = request.POST.get("target_document_type", "")
        try:
            new_template = duplicate_template(
                user=request.user, source_document_type=document_type, target_document_type=target
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("documents:template_edit", document_type=document_type)

        messages.success(
            request,
            f"Duplicated into a new {new_template.get_document_type_display()} draft template.",
        )
        return redirect("documents:template_edit", document_type=new_template.document_type)


class DocumentTemplateApplyStarterView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Creates document_type's first template from one of the packaged
    starter layouts (apps.documents.gallery.STARTER_TEMPLATES) — the
    editor screen only offers this when the type has no template yet (same
    "reset first" rule as Duplicate).
    """

    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, document_type):
        _require_valid_document_type(document_type)
        preset_key = request.POST.get("preset_key", "")
        try:
            apply_starter_template(
                user=request.user, document_type=document_type, preset_key=preset_key
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Started a new draft from the selected layout.")
        return redirect("documents:template_edit", document_type=document_type)


class DocumentTemplateRestoreVersionView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Restores an old DocumentTemplateVersion snapshot as a new save on the
    live row — never edits the version history itself.
    """

    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, document_type, version_pk):
        _require_valid_document_type(document_type)
        version_obj = get_object_or_404(
            DocumentTemplateVersion.objects.select_related("template"),
            pk=version_pk,
            template__document_type=document_type,
        )
        try:
            restore_template_version(user=request.user, version_obj=version_obj)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Restored v{version_obj.version}.")
        return redirect("documents:template_edit", document_type=document_type)


class PdfHealthDiagnosticsView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Administrator-only visibility into apps.documents.services.
    generate_document()'s DocumentRenderLog entries (duration, template/
    version, failures) and the latest scheduled DocumentIntegrityCheckRun
    (apps.documents.integrity, run via the check_document_integrity
    management command) — spec: "record render duration, template version,
    failures, and storage errors... reporting missing files before a user
    discovers them."
    """

    allowed_roles = (ADMINISTRATOR,)
    template_name = "documents/pdf_health.html"
    LOG_LIMIT = 200

    def get(self, request):
        logs = DocumentRenderLog.objects.select_related(
            "template", "generated_document", "triggered_by"
        )[: self.LOG_LIMIT]
        recent_failures = DocumentRenderLog.objects.filter(success=False).count()
        recent_total = DocumentRenderLog.objects.count()
        latest_integrity_run = DocumentIntegrityCheckRun.objects.first()
        missing_documents = (
            GeneratedDocument.objects.filter(
                pk__in=latest_integrity_run.missing_document_ids
            ).select_related("transaction")
            if latest_integrity_run and latest_integrity_run.missing_document_ids
            else GeneratedDocument.objects.none()
        )
        return render(
            request,
            self.template_name,
            {
                "logs": logs,
                "recent_failures": recent_failures,
                "recent_total": recent_total,
                "latest_integrity_run": latest_integrity_run,
                "missing_documents": missing_documents,
            },
        )


class CountryBrandingListView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Every Country-level location, with whether it already has a branding
    profile — the entry point into apps.documents.branding, linked from both
    the document template hub and the Settings hub.
    """

    allowed_roles = (ADMINISTRATOR,)
    template_name = "documents/branding_list.html"

    def get(self, request):
        profiles = list_branding_profiles()
        rows = [
            {"country": country, "profile": profiles.get(country.id)}
            for country in eligible_countries()
        ]
        return render(request, self.template_name, {"rows": rows})


class CountryBrandingEditView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Create/update the one branding profile for a single country — a plain
    save, unlike DocumentTemplateEditView's Draft/Publish workflow: a
    branding override is identity data (logo, address, terms wording), not a
    structural layout change, so it takes effect on the next generated
    document immediately, the same as an ordinary edit to an already-
    published template.
    """

    allowed_roles = (ADMINISTRATOR,)
    template_name = "documents/branding_edit.html"

    def _country(self, country_id):
        return get_object_or_404(Location, pk=country_id, level=LocationLevel.COUNTRY)

    def get(self, request, country_id):
        country = self._country(country_id)
        profile = get_branding_profile(country)
        form = CountryBrandingForm(initial=self._initial(profile))
        return self._render(request, country, form, profile)

    def post(self, request, country_id):
        country = self._country(country_id)
        profile = get_branding_profile(country)
        form = CountryBrandingForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._render(request, country, form, profile)

        data = form.cleaned_data
        try:
            save_branding_profile(
                user=request.user,
                country=country,
                logo=data.get("logo"),
                remove_logo=data.get("remove_logo", False),
                company_name=data["company_name"],
                company_address=data["company_address"],
                company_tax_id=data["company_tax_id"],
                terms_text=data["terms_text"],
                signature_left_label=data["signature_left_label"],
                signature_right_label=data["signature_right_label"],
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
            return self._render(request, country, form, profile)

        messages.success(request, f"Branding profile saved for {country}.")
        return redirect("documents:branding_edit", country_id=country_id)

    @staticmethod
    def _initial(profile):
        if profile is None:
            return {}
        return {
            "company_name": profile.company_name,
            "company_address": profile.company_address,
            "company_tax_id": profile.company_tax_id,
            "terms_text": profile.terms_text,
            "signature_left_label": profile.signature_left_label,
            "signature_right_label": profile.signature_right_label,
        }

    def _render(self, request, country, form, profile):
        return render(
            request,
            self.template_name,
            {"form": form, "country": country, "profile": profile},
        )
