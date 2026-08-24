from django.contrib import admin

from .models import Location


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    """Read-only in Django admin — creation/deactivation must go through the
    app's own screens (apps/locations/views.py) so every change is audited.
    """

    list_display = ("name", "level", "parent", "is_active", "path")
    list_filter = ("level", "is_active")
    search_fields = ("name", "code")
    readonly_fields = [f.name for f in Location._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
