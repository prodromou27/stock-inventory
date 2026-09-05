from django.contrib import admin

from .models import (
    AssetStatusHistory,
    Customer,
    InventoryTransaction,
    InventoryTransactionLine,
    ProductLocationThreshold,
    StockBalance,
    UnitAsset,
)


class ReadOnlyAdminMixin:
    """These rows are only ever written by apps.inventory.services (the
    ledger) so every write is validated and audited — no admin add/edit.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    """Unlike every other model in this file, Customer isn't ledger/balance
    data — it's a small, freely-editable lookup table (name + optional
    reference), so ordinary admin add/change/delete applies normally.
    """

    list_display = ("name", "reference", "is_active")
    search_fields = ("name", "reference")
    list_filter = ("is_active",)


@admin.register(UnitAsset)
class UnitAssetAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "status", "current_location", "arrival_date")
    list_filter = ("status",)
    search_fields = ("vendor_serial", "normalized_serial", "product__model", "product__brand__name")


@admin.register(StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
    """Not read-only — StockBalance is a mutable running balance by design,
    but it's still only ever written through the ledger service, never via
    this admin (no add/change permission either, for the same reason).
    """

    list_display = ("product", "location", "on_hand_quantity", "reserved_quantity")
    search_fields = ("product__model", "product__brand__name", "location__name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("transaction_number", "movement_type", "occurred_at", "performed_by")
    list_filter = ("movement_type",)
    search_fields = ("transaction_number", "project_reference", "final_customer")


@admin.register(InventoryTransactionLine)
class InventoryTransactionLineAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("transaction", "line_number", "product", "unit_asset", "quantity_delta")
    search_fields = ("transaction__transaction_number", "product__model")


@admin.register(AssetStatusHistory)
class AssetStatusHistoryAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("unit_asset", "from_status", "to_status", "occurred_at")


@admin.register(ProductLocationThreshold)
class ProductLocationThresholdAdmin(admin.ModelAdmin):
    """Sparse, per-location reorder overrides — see the model's docstring
    for why this is plain admin CRUD rather than a dedicated screen: rare,
    low-volume configuration data, not something operators touch daily the
    way Product's own global reorder fields (edited on the ordinary product
    form) are.
    """

    list_display = (
        "product",
        "location",
        "target_stock_level",
        "min_reorder_quantity",
        "preferred_supplier",
    )
    search_fields = ("product__model", "product__brand__name", "location__name")
    autocomplete_fields = ("product", "location")
