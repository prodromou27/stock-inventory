from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.views import View
from django.views.generic import ListView

from apps.core.csv_export import CSVExportMixin
from apps.inventory.models import UnitAsset

from . import queries


class ReportsHubView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, "reporting/hub.html")


class CurrentStockView(LoginRequiredMixin, View):
    def get(self, request):
        units, balances = queries.current_stock(request.user)
        return render(
            request, "reporting/current_stock.html", {"units": units, "balances": balances}
        )


class StockByLocationView(LoginRequiredMixin, View):
    def get(self, request):
        rows = queries.stock_by_location(request.user)
        return render(request, "reporting/stock_by_location.html", {"rows": rows})


class ReservedStockView(LoginRequiredMixin, View):
    def get(self, request):
        units, reservations = queries.reserved_stock(request.user)
        return render(
            request,
            "reporting/reserved_stock.html",
            {"units": units, "reservations": reservations},
        )


class EmployeeAssignmentsView(LoginRequiredMixin, CSVExportMixin, ListView):
    template_name = "reporting/employee_assignments.html"
    context_object_name = "transactions"
    paginate_by = 50
    csv_filename = "employee_assignments.csv"
    csv_headers = [
        "Transaction",
        "Date",
        "Employee",
        "Project Reference",
        "Temporary",
        "Performed By",
    ]

    def get_queryset(self):
        return queries.employee_assignments(self.request.user)

    def csv_rows(self, queryset):
        for txn in queryset:
            yield [
                txn.transaction_number,
                txn.occurred_at.isoformat(),
                txn.employee_name,
                txn.project_reference,
                "Yes" if txn.is_temporary_assignment else "No",
                txn.performed_by.get_username(),
            ]


class CustomerDeliveriesView(LoginRequiredMixin, CSVExportMixin, ListView):
    template_name = "reporting/customer_deliveries.html"
    context_object_name = "transactions"
    paginate_by = 50
    csv_filename = "customer_deliveries.csv"
    csv_headers = ["Transaction", "Date", "Final Customer", "Project Reference", "Performed By"]

    def get_queryset(self):
        return queries.customer_deliveries(self.request.user)

    def csv_rows(self, queryset):
        for txn in queryset:
            yield [
                txn.transaction_number,
                txn.occurred_at.isoformat(),
                txn.final_customer,
                txn.project_reference,
                txn.performed_by.get_username(),
            ]


class StockByProjectReferenceView(LoginRequiredMixin, View):
    def get(self, request):
        project_reference = request.GET.get("project_reference", "").strip()
        units, reservations = queries.stock_by_project_reference(request.user, project_reference)
        return render(
            request,
            "reporting/stock_by_project_reference.html",
            {
                "project_reference": project_reference,
                "units": units if project_reference else None,
                "reservations": reservations,
                "distinct_references": units if not project_reference else None,
            },
        )


class TemporaryAssignmentsView(LoginRequiredMixin, ListView):
    template_name = "reporting/temporary_assignments.html"
    context_object_name = "transactions"
    paginate_by = 50

    def get_queryset(self):
        return queries.temporary_assignments(self.request.user)


class DamagedAssetsView(LoginRequiredMixin, CSVExportMixin, ListView):
    template_name = "reporting/damaged_assets.html"
    context_object_name = "assets"
    paginate_by = 50
    csv_filename = "damaged_assets.csv"
    csv_headers = ["Brand", "Model", "SKU", "Type", "Serial", "Location", "Notes"]

    def get_queryset(self):
        return queries.damaged_assets(self.request.user)

    def csv_rows(self, queryset):
        for asset in queryset:
            yield [
                asset.product.brand.name,
                asset.product.model,
                asset.product.sku,
                asset.product.product_type.name,
                asset.vendor_serial,
                str(asset.current_location or ""),
                asset.notes,
            ]


class LostAssetsView(LoginRequiredMixin, CSVExportMixin, ListView):
    template_name = "reporting/lost_assets.html"
    context_object_name = "assets"
    paginate_by = 50
    csv_filename = "lost_assets.csv"
    csv_headers = ["Brand", "Model", "SKU", "Type", "Serial", "Last Removal Date", "Notes"]

    def get_queryset(self):
        return queries.lost_assets(self.request.user)

    def csv_rows(self, queryset):
        for asset in queryset:
            yield [
                asset.product.brand.name,
                asset.product.model,
                asset.product.sku,
                asset.product.product_type.name,
                asset.vendor_serial,
                asset.last_removal_date.isoformat() if asset.last_removal_date else "",
                asset.notes,
            ]


class DisposedItemsView(LoginRequiredMixin, CSVExportMixin, ListView):
    """Optimized for reviewing disposed HDDs (spec §9/§15) — the Type column
    (from product_type) is what identifies them; disposed assets are never
    deleted, so this remains complete indefinitely.
    """

    template_name = "reporting/disposed_items.html"
    context_object_name = "assets"
    paginate_by = 50
    csv_filename = "disposed_items.csv"
    csv_headers = [
        "Brand",
        "Model",
        "SKU",
        "Type",
        "Serial",
        "Project Reference",
        "Final Customer",
        "Disposal Date",
        "Notes",
    ]

    def get_queryset(self):
        queryset = queries.disposed_items(self.request.user)
        if product_type := self.request.GET.get("type", "").strip():
            queryset = queryset.filter(product__product_type__name__icontains=product_type)
        return queryset

    def csv_rows(self, queryset):
        for asset in queryset:
            yield [
                asset.product.brand.name,
                asset.product.model,
                asset.product.sku,
                asset.product.product_type.name,
                asset.vendor_serial,
                asset.project_reference,
                asset.final_customer,
                asset.last_removal_date.isoformat() if asset.last_removal_date else "",
                asset.notes,
            ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["type_filter"] = self.request.GET.get("type", "")
        return context


class MovementHistoryView(LoginRequiredMixin, CSVExportMixin, ListView):
    template_name = "reporting/movement_history.html"
    context_object_name = "events"
    paginate_by = 50
    csv_filename = "movement_history.csv"
    csv_headers = [
        "When",
        "Asset",
        "Serial",
        "From Status",
        "To Status",
        "From Location",
        "To Location",
        "Transaction",
    ]

    def get_queryset(self):
        unit_asset = None
        if asset_id := self.request.GET.get("asset"):
            unit_asset = UnitAsset.objects.filter(pk=asset_id).first()
        return queries.movement_history(self.request.user, unit_asset=unit_asset)

    def csv_rows(self, queryset):
        for event in queryset:
            yield [
                event.occurred_at.isoformat(),
                str(event.unit_asset.product),
                event.unit_asset.vendor_serial,
                event.from_status or "",
                event.to_status,
                str(event.from_location or ""),
                str(event.to_location or ""),
                event.transaction.transaction_number,
            ]


class LowStockView(LoginRequiredMixin, ListView):
    template_name = "reporting/low_stock.html"
    context_object_name = "balances"
    paginate_by = 50

    def get_queryset(self):
        return queries.low_stock_balances(self.request.user)
