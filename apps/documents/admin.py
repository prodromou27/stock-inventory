from django.contrib import admin

from .models import Attachment, GeneratedDocument


class ReadOnlyAdminMixin:
    """These rows are only ever written by apps.documents.services, so every
    write is validated and audited — no admin add/edit.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GeneratedDocument)
class GeneratedDocumentAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "document_number",
        "document_type",
        "transaction",
        "generated_by",
        "generated_at",
    )
    list_filter = ("document_type",)
    search_fields = ("document_number", "transaction__transaction_number")


@admin.register(Attachment)
class AttachmentAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("original_filename", "transaction", "uploaded_by", "uploaded_at", "is_deleted")
    list_filter = ("is_deleted",)
    search_fields = ("original_filename", "transaction__transaction_number")
