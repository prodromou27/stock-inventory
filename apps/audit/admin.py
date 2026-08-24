from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Read-only by design — AuditEvent rows are append-only (see models.py)."""

    list_display = ("occurred_at", "event_type", "actor", "object_type", "object_id", "summary")
    list_filter = ("event_type",)
    search_fields = ("summary", "object_type", "object_id")
    date_hierarchy = "occurred_at"
    readonly_fields = [f.name for f in AuditEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
