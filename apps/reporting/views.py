import csv

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.core.csv_export import CSVExportMixin
from apps.inventory.models import UnitAsset

from . import queries
from .forms import ReportBaseModelForm, ReportBuilderForm, ReportFilterFormSet
from .models import ReportBaseModel, SavedReport
from .report_builder import REPORTABLE_FIELDS, build_queryset, friendly_rows
from .services import create_saved_report, delete_saved_report

REPORT_PAGE_SIZE = 50
SAVED_REPORT_ROW_CAP = 1000


class ReportsHubView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, "reporting/hub.html")


class CurrentStockView(LoginRequiredMixin, View):
    """Units in stock is paginated (spec §21.15: every list must stay
    responsive at 8,000+ records) — caught by measuring real wall-clock
    timing against the bulk-seeded dataset during Prompt 8, not by the
    query-count tests alone, which never exercised this plain View. Balances
    is left unpaginated: it's grouped one row per (product, location), an
    inherently much smaller set than the raw unit count.
    """

    def get(self, request):
        units, balances = queries.current_stock(request.user)
        page_obj = Paginator(units, REPORT_PAGE_SIZE).get_page(request.GET.get("page"))
        return render(
            request,
            "reporting/current_stock.html",
            {
                "units": page_obj,
                "page_obj": page_obj,
                "is_paginated": page_obj.has_other_pages(),
                "balances": balances,
            },
        )


class StockByLocationView(LoginRequiredMixin, View):
    def get(self, request):
        rows = queries.stock_by_location(request.user)
        return render(request, "reporting/stock_by_location.html", {"rows": rows})


class ReservedStockView(LoginRequiredMixin, View):
    def get(self, request):
        units, reservations = queries.reserved_stock(request.user)
        page_obj = Paginator(units, REPORT_PAGE_SIZE).get_page(request.GET.get("page"))
        return render(
            request,
            "reporting/reserved_stock.html",
            {
                "units": page_obj,
                "page_obj": page_obj,
                "is_paginated": page_obj.has_other_pages(),
                "reservations": reservations,
            },
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

        page_obj = None
        if project_reference:
            page_obj = Paginator(units, REPORT_PAGE_SIZE).get_page(request.GET.get("page"))

        return render(
            request,
            "reporting/stock_by_project_reference.html",
            {
                "project_reference": project_reference,
                "units": page_obj if project_reference else None,
                "page_obj": page_obj,
                "is_paginated": page_obj.has_other_pages() if page_obj else False,
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


# --- Ad-hoc report builder ("more structured reporting", user request) ---


class SavedReportListView(LoginRequiredMixin, View):
    """A user's own saved reports, plus anything an Administrator has
    shared — never another user's private (unshared) report.
    """

    def get(self, request):
        reports = SavedReport.objects.filter(
            Q(created_by=request.user) | Q(is_shared=True)
        ).select_related("created_by")
        return render(request, "reporting/saved_report_list.html", {"reports": reports})


class ReportBuilderStartView(LoginRequiredMixin, View):
    """Step 1: which model to report on — a separate step from
    ReportBuilderView because the field/filter choices there depend on
    this, and there's no JS to refresh a dropdown's options in place.
    """

    def get(self, request):
        return render(request, "reporting/builder_start.html", {"form": ReportBaseModelForm()})

    def post(self, request):
        form = ReportBaseModelForm(request.POST)
        if not form.is_valid():
            return render(request, "reporting/builder_start.html", {"form": form})
        url = reverse("reporting:builder")
        return redirect(f"{url}?base_model={form.cleaned_data['base_model']}")


class ReportBuilderView(LoginRequiredMixin, View):
    """Step 2: pick columns and (optionally) filters, then save. Saving
    immediately runs the report (redirects to SavedReportRunView) — there's
    no separate "preview" step, matching this app's "must be useful, not
    time consuming" bias toward the simplest thing that actually works.
    """

    template_name = "reporting/builder.html"

    def _base_model(self, request):
        base_model = request.GET.get("base_model") or request.POST.get("base_model")
        return base_model if base_model in REPORTABLE_FIELDS else None

    def get(self, request):
        base_model = self._base_model(request)
        if base_model is None:
            return redirect("reporting:builder_start")
        form = ReportBuilderForm(base_model=base_model)
        filter_formset = ReportFilterFormSet(form_kwargs={"base_model": base_model})
        return self._render(request, base_model, form, filter_formset)

    def post(self, request):
        base_model = self._base_model(request)
        if base_model is None:
            return redirect("reporting:builder_start")

        form = ReportBuilderForm(request.POST, base_model=base_model)
        filter_formset = ReportFilterFormSet(request.POST, form_kwargs={"base_model": base_model})
        if not (form.is_valid() and filter_formset.is_valid()):
            return self._render(request, base_model, form, filter_formset)

        filters = [
            {"field_key": row["field_key"], "op": row.get("op") or "exact", "value": row["value"]}
            for row in filter_formset.cleaned_data
            if row.get("field_key") and row.get("value")
        ]
        try:
            report = create_saved_report(
                user=request.user,
                name=form.cleaned_data["name"],
                base_model=base_model,
                selected_fields=form.cleaned_data["selected_fields"],
                filters=filters,
                is_shared=form.cleaned_data["is_shared"],
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
            return self._render(request, base_model, form, filter_formset)

        messages.success(request, f"Saved report '{report.name}'.")
        return redirect("reporting:saved_report_run", pk=report.pk)

    def _render(self, request, base_model, form, filter_formset):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "filter_formset": filter_formset,
                "base_model": base_model,
                "base_model_label": dict(ReportBaseModel.choices).get(base_model, base_model),
            },
        )


class SavedReportRunView(LoginRequiredMixin, View):
    """Runs a saved report — its own creator, or anyone if it's shared.
    Capped at SAVED_REPORT_ROW_CAP rows (no pagination UI for a first
    version); `?format=csv` streams the same capped row set as CSV.
    """

    def get(self, request, pk):
        report = get_object_or_404(SavedReport, pk=pk)
        if report.created_by_id != request.user.id and not report.is_shared:
            raise Http404("Report not found.")

        columns, queryset = build_queryset(
            user=request.user,
            base_model=report.base_model,
            selected_fields=report.selected_fields,
            filters=report.filters,
        )
        raw_rows = list(queryset[: SAVED_REPORT_ROW_CAP + 1])
        truncated = len(raw_rows) > SAVED_REPORT_ROW_CAP
        dict_rows = friendly_rows(columns, report.base_model, raw_rows[:SAVED_REPORT_ROW_CAP])
        # Reshaped from {column: value} dicts into plain value-lists (same
        # order as `columns`) so the template can render a row with a
        # simple {% for %} — Django template dot-lookup can't index a dict
        # by a loop variable, only by a literal key.
        rows = [[row.get(column, "") for column in columns] for row in dict_rows]

        if request.GET.get("format") == "csv":
            return self._csv_response(report, columns, rows)

        return render(
            request,
            "reporting/saved_report_run.html",
            {
                "report": report,
                "columns": columns,
                "rows": rows,
                "row_cap": SAVED_REPORT_ROW_CAP,
                "truncated": truncated,
                "can_delete": report.created_by_id == request.user.id,
            },
        )

    def _csv_response(self, report, columns, rows):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{report.name}.csv"'
        writer = csv.writer(response)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(row)
        return response


class SavedReportDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        report = get_object_or_404(SavedReport, pk=pk)
        try:
            delete_saved_report(report=report, user=request.user)
        except PermissionDenied:
            raise Http404("Report not found.") from None
        messages.success(request, f"Deleted report '{report.name}'.")
        return redirect("reporting:saved_report_list")
