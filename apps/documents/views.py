from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, RoleRequiredMixin
from apps.inventory.access import require_transaction_access
from apps.inventory.models import InventoryTransaction

from .forms import AttachmentUploadForm
from .models import Attachment, GeneratedDocument
from .services import delete_attachment, generate_document, regenerate_document, upload_attachment


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
