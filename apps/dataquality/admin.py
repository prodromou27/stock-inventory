from django.contrib import admin

from .models import DataQualityFinding


@admin.register(DataQualityFinding)
class DataQualityFindingAdmin(admin.ModelAdmin):
    list_display = ("issue_type", "severity", "status", "object_type", "object_id", "detected_at")
    list_filter = ("issue_type", "severity", "status")
    search_fields = ("object_id", "explanation")
    readonly_fields = [f.name for f in DataQualityFinding._meta.fields]
