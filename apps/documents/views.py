from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, RoleRequiredMixin
from apps.inventory.access import require_transaction_access
from apps.inventory.models import InventoryTransaction

from .forms import AttachmentUploadForm, DocumentTemplateStyleForm
from .models import Attachment, DocumentType, GeneratedDocument
from .pdf import render_pdf, render_styleable_source, sample_document_context
from .services import delete_attachment, generate_document, regenerate_document, upload_attachment
from .template_services import get_template, render_preview_pdf, reset_template, update_template


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

        return FileResponse(
            document.pdf_file.open("rb"),
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
        pdf_bytes = render_pdf(sample_document_context(), document_type=document_type)
        return HttpResponse(pdf_bytes, content_type="application/pdf")


def _require_valid_document_type(document_type):
    if document_type not in DocumentType.values:
        raise Http404("Unknown document type.")


class DocumentTemplateEditView(LoginRequiredMixin, RoleRequiredMixin, View):
    """A structured branding panel, not an HTML editor — logo, its position,
    an accent color, a font, and page margins are the only things an
    Administrator chooses. The report's actual data fields are always
    placed automatically by the packaged skeleton (apps.documents.pdf.
    render_styleable_source()); nothing here is typed as template syntax.
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
        try:
            update_template(
                user=request.user,
                document_type=document_type,
                html_source=render_styleable_source(
                    logo_position=data["logo_position"],
                    accent_color=data["accent_color"],
                    font_choice=data["font_choice"],
                    page_margin=data["page_margin"],
                ),
                logo=data.get("logo"),
                remove_logo=data.get("remove_logo", False),
                logo_position=data["logo_position"],
                accent_color=data["accent_color"],
                font_choice=data["font_choice"],
                page_margin=data["page_margin"],
                layout_config=form.layout_config(),
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
            return self._render(request, document_type, form, template_obj)

        messages.success(request, "Template saved.")
        return redirect("documents:template_edit", document_type=document_type)

    @staticmethod
    def _initial(template_obj):
        if template_obj is None:
            return {}
        return {
            "logo_position": template_obj.logo_position,
            "accent_color": template_obj.accent_color,
            "font_choice": template_obj.font_choice,
            "page_margin": template_obj.page_margin,
            **template_obj.layout_config,
        }

    def _render(self, request, document_type, form, template_obj):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "document_type": document_type,
                "document_type_label": dict(DocumentType.choices)[document_type],
                "template_obj": template_obj,
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
                layout_config=form.layout_config(),
            )
        except ValidationError as exc:
            return HttpResponseBadRequest(
                "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            )
        return HttpResponse(pdf_bytes, content_type="application/pdf")


class DocumentTemplateResetView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, document_type):
        _require_valid_document_type(document_type)
        reset_template(user=request.user, document_type=document_type)
        messages.success(request, "Template reset to the packaged default.")
        return redirect("documents:template_edit", document_type=document_type)
