from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, RoleRequiredMixin
from apps.locations.scoping import require_location_access, scope_queryset

from .forms import ReceiveStockForm
from .models import (
    InventoryTransaction,
    InventoryTransactionLine,
    StockBalance,
    UnitAsset,
    UnitStatus,
)
from .services.receipts import DuplicateSerialError, receive_stock


class ReceiveStockView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def get(self, request):
        initial = {}
        product_id = request.GET.get("product")
        if product_id:
            initial["product"] = product_id
        form = ReceiveStockForm(user=request.user, initial=initial)
        return render(request, "inventory/receive_stock_form.html", {"form": form})

    def post(self, request):
        form = ReceiveStockForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, "inventory/receive_stock_form.html", {"form": form})

        data = form.cleaned_data
        try:
            txn = receive_stock(
                user=request.user,
                product=data["product"],
                location=data["location"],
                occurred_at=data["occurred_at"],
                vendor_serial=data["vendor_serial"],
                quantity=data["quantity"],
                project_reference=data["project_reference"],
                final_customer=data["final_customer"],
                supplier=data["supplier"],
                invoice_number=data["invoice_number"],
                condition=data["condition"],
                accessories=data["accessories"],
                notes=data["notes"],
                duplicate_serial_acknowledged=request.POST.get("duplicate_serial_acknowledged")
                == "true",
            )
        except DuplicateSerialError as exc:
            return render(
                request,
                "inventory/receive_stock_form.html",
                {"form": form, "duplicate_matches": exc.matches, "show_duplicate_warning": True},
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, "inventory/receive_stock_form.html", {"form": form})

        messages.success(request, f"Received stock — transaction {txn.transaction_number}.")
        return redirect(txn.get_absolute_url())


class UnitAssetListView(LoginRequiredMixin, ListView):
    model = UnitAsset
    template_name = "inventory/asset_list.html"
    context_object_name = "assets"
    paginate_by = 50

    def get_queryset(self):
        queryset = scope_queryset(
            self.request.user,
            UnitAsset.objects.select_related("product", "product__brand", "current_location"),
            location_field="current_location",
        )
        product_id = self.request.GET.get("product")
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        status = self.request.GET.get("status")
        if status:
            queryset = queryset.filter(status=status)
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(normalized_serial__icontains=query.upper())
        return queryset.order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["query"] = self.request.GET.get("q", "")
        context["selected_status"] = self.request.GET.get("status", "")
        context["statuses"] = UnitStatus.choices
        return context


class UnitAssetDetailView(LoginRequiredMixin, DetailView):
    model = UnitAsset
    template_name = "inventory/asset_detail.html"
    context_object_name = "asset"

    def get_object(self, queryset=None):
        obj = get_object_or_404(
            UnitAsset.objects.select_related("product", "product__brand", "current_location"),
            pk=self.kwargs["pk"],
        )
        require_location_access(self.request.user, obj.current_location)
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["history"] = self.object.status_history.select_related(
            "transaction", "from_location", "to_location", "recorded_by"
        )
        return context


class StockBalanceListView(LoginRequiredMixin, ListView):
    model = StockBalance
    template_name = "inventory/balance_list.html"
    context_object_name = "balances"
    paginate_by = 50

    def get_queryset(self):
        queryset = scope_queryset(
            self.request.user,
            StockBalance.objects.select_related("product", "product__brand", "location"),
            location_field="location",
        )
        product_id = self.request.GET.get("product")
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        return queryset.order_by("product__brand__name", "product__model", "location__name")


class StockBalanceDetailView(LoginRequiredMixin, DetailView):
    model = StockBalance
    template_name = "inventory/balance_detail.html"
    context_object_name = "balance"

    def get_object(self, queryset=None):
        obj = get_object_or_404(
            StockBalance.objects.select_related("product", "product__brand", "location"),
            pk=self.kwargs["pk"],
        )
        require_location_access(self.request.user, obj.location)
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["lines"] = (
            InventoryTransactionLine.objects.filter(product=self.object.product)
            .filter(Q(from_location=self.object.location) | Q(to_location=self.object.location))
            .select_related("transaction")
            .order_by("transaction__occurred_at", "transaction__transaction_number")
        )
        return context


class TransactionDetailView(LoginRequiredMixin, DetailView):
    model = InventoryTransaction
    template_name = "inventory/transaction_detail.html"
    context_object_name = "transaction"

    def get_object(self, queryset=None):
        obj = get_object_or_404(
            InventoryTransaction.objects.select_related(
                "performed_by", "source_location", "destination_location"
            ),
            pk=self.kwargs["pk"],
        )
        self._require_transaction_access(obj)
        return obj

    def _require_transaction_access(self, txn):
        locations = [
            loc for loc in (txn.destination_location, txn.source_location) if loc is not None
        ]
        if not locations:
            return
        for location in locations:
            try:
                require_location_access(self.request.user, location)
                return
            except PermissionDenied:
                continue
        raise PermissionDenied("You do not have access to this transaction.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["lines"] = self.object.lines.select_related("unit_asset", "product").order_by(
            "line_number"
        )
        return context
