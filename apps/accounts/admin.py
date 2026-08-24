from django.contrib import admin

from .models import UserLocationAccess


@admin.register(UserLocationAccess)
class UserLocationAccessAdmin(admin.ModelAdmin):
    """Read-only in Django admin — grant/revoke must go through the app's own
    screens (apps/accounts/views.py) so every change is audited.
    """

    list_display = ("user", "location", "granted_by", "granted_at")
    search_fields = ("user__username", "location__name")
    readonly_fields = [f.name for f in UserLocationAccess._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
