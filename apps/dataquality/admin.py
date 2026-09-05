from django.contrib import admin

from .models import DataQualityFinding


@admin.register(DataQualityFinding)
class DataQualityFindingAdmin(admin.ModelAdmin):
    """Findings are only ever written by the detection scan
    (apps.dataquality.services.run_detection()) and resolved/dismissed
    through its own service functions — never created, edited, or deleted
    from Django admin, same as every other ledger-adjacent admin
    registration in this app.
    """

    list_display = ("issue_type", "severity", "status", "object_type", "object_id", "detected_at")
    list_filter = ("issue_type", "severity", "status")
    search_fields = ("object_id", "explanation")
    readonly_fields = [f.name for f in DataQualityFinding._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
