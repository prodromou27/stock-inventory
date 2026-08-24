from django.contrib import admin

from .models import Brand, Product, ProductType


class ReadOnlyAdminMixin:
    """Creation/edits must go through the app's own screens (views.py) so
    every change is validated and audited.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Brand)
class BrandAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "is_active")
    search_fields = ("name",)


@admin.register(ProductType)
class ProductTypeAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "is_active")
    search_fields = ("name",)


@admin.register(Product)
class ProductAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "brand", "product_type", "tracking_method", "is_active")
    list_filter = ("tracking_method", "is_active", "product_type")
    search_fields = ("model", "sku", "brand__name")
