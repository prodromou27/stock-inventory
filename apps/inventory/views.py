from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.catalog.models import Product
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, RoleRequiredMixin
from apps.core.csv_export import CSVExportMixin
from apps.locations.scoping import accessible_locations, require_location_access, scope_queryset

from .access import require_transaction_access, scope_transaction_queryset
from .filters import filter_stock_balances, filter_unit_assets
from .forms import (
    AdminCorrectBalanceForm,
    AdminCorrectUnitForm,
    AdminReversalForm,
    AssignForm,
    DeliverForm,
    DispositionForm,
    ReceiveStockForm,
    ReserveForm,
    ReturnAssessmentForm,
    ReturnForm,
    TransferForm,
)
from .models import (
    InventoryTransaction,
    InventoryTransactionLine,
    MovementType,
    ReservationStatus,
    StockBalance,
    StockReservation,
    UnitAsset,
    UnitStatus,
)
from .services.assignments import assign_to_employee, deliver_to_customer
from .services.corrections import correct_balance, correct_unit_status, reverse_transaction
from .services.disposition import dispose, mark_damaged, mark_lost
from .services.receipts import DuplicateSerialError, receive_stock
from .services.reservations import release_reservation, reserve_stock
from .services.returns import assess_return, return_stock
from .services.transfers import bulk_transfer


class MovementsHubView(LoginRequiredMixin, RoleRequiredMixin, View):
    """A simple index of the movement workflows, so the top nav doesn't need
    a link per workflow.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def get(self, request):
        return render(request, "inventory/movements_hub.html")


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


class UnitAssetListView(LoginRequiredMixin, CSVExportMixin, ListView):
    model = UnitAsset
    template_name = "inventory/asset_list.html"
    context_object_name = "assets"
    paginate_by = 50
    csv_filename = "assets.csv"
    csv_headers = [
        "Brand",
        "Model",
        "SKU",
        "Type",
        "Serial",
        "Status",
        "Location",
        "Project Reference",
        "Final Customer",
        "Arrival Date",
        "Removal Date",
    ]

    def get_queryset(self):
        queryset = scope_queryset(
            self.request.user,
            UnitAsset.objects.select_related(
                "product", "product__brand", "product__product_type", "current_location"
            ),
            location_field="current_location",
        )
        product_id = self.request.GET.get("product")
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        queryset = filter_unit_assets(queryset, self.request.GET)
        return queryset.order_by("-created_at")

    def csv_rows(self, queryset):
        for asset in queryset:
            yield [
                asset.product.brand.name,
                asset.product.model,
                asset.product.sku,
                asset.product.product_type.name,
                asset.vendor_serial,
                asset.get_status_display(),
                str(asset.current_location or ""),
                asset.project_reference,
                asset.final_customer,
                asset.arrival_date.isoformat() if asset.arrival_date else "",
                asset.last_removal_date.isoformat() if asset.last_removal_date else "",
            ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["query"] = self.request.GET.get("q", "")
        context["selected_status"] = self.request.GET.get("status", "")
        context["statuses"] = UnitStatus.choices
        context["locations"] = accessible_locations(self.request.user).order_by("level", "name")
        context["filters"] = self.request.GET
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


class StockBalanceListView(LoginRequiredMixin, CSVExportMixin, ListView):
    model = StockBalance
    template_name = "inventory/balance_list.html"
    context_object_name = "balances"
    paginate_by = 50
    csv_filename = "stock_balances.csv"
    csv_headers = ["Brand", "Model", "SKU", "Type", "Location", "On Hand", "Reserved", "Available"]

    def get_queryset(self):
        queryset = scope_queryset(
            self.request.user,
            StockBalance.objects.select_related(
                "product", "product__brand", "product__product_type", "location"
            ),
            location_field="location",
        )
        product_id = self.request.GET.get("product")
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        queryset = filter_stock_balances(queryset, self.request.GET)
        return queryset.order_by("product__brand__name", "product__model", "location__name")

    def csv_rows(self, queryset):
        for balance in queryset:
            yield [
                balance.product.brand.name,
                balance.product.model,
                balance.product.sku,
                balance.product.product_type.name,
                str(balance.location),
                balance.on_hand_quantity,
                balance.reserved_quantity,
                balance.available_quantity,
            ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["locations"] = accessible_locations(self.request.user).order_by("level", "name")
        context["filters"] = self.request.GET
        return context


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


class TransactionListView(LoginRequiredMixin, ListView):
    """The "Transactions and documents" screen (spec §14)."""

    model = InventoryTransaction
    template_name = "inventory/transaction_list.html"
    context_object_name = "transactions"
    paginate_by = 50

    def get_queryset(self):
        queryset = scope_transaction_queryset(
            self.request.user,
            InventoryTransaction.objects.select_related(
                "performed_by", "source_location", "destination_location"
            ),
        )
        if movement_type := self.request.GET.get("movement_type", "").strip():
            queryset = queryset.filter(movement_type=movement_type)
        if project_reference := self.request.GET.get("project_reference", "").strip():
            queryset = queryset.filter(project_reference__icontains=project_reference)
        if final_customer := self.request.GET.get("final_customer", "").strip():
            queryset = queryset.filter(final_customer__icontains=final_customer)
        if occurred_after := self.request.GET.get("occurred_after", "").strip():
            queryset = queryset.filter(occurred_at__gte=occurred_after)
        if occurred_before := self.request.GET.get("occurred_before", "").strip():
            queryset = queryset.filter(occurred_at__lte=occurred_before)
        return queryset.order_by("-occurred_at", "-transaction_number")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["movement_types"] = MovementType.choices
        context["selected_movement_type"] = self.request.GET.get("movement_type", "")
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
        require_transaction_access(self.request.user, obj)
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["lines"] = self.object.lines.select_related("unit_asset", "product").order_by(
            "line_number"
        )
        context["can_return"] = self.object.movement_type in (
            MovementType.ASSIGNMENT,
            MovementType.DELIVERY,
        )
        context["already_reversed"] = InventoryTransaction.objects.filter(
            related_transaction=self.object, movement_type=MovementType.REVERSAL
        ).exists()
        context["is_reversal_target"] = self.object.movement_type not in (
            MovementType.CORRECTION,
            MovementType.REVERSAL,
        )
        # Reverse relations from apps.documents — accessed without an import
        # to avoid inventory depending on documents (docs/architecture/01).
        context["documents"] = self.object.generated_documents.order_by("-generated_at")
        context["attachments"] = self.object.attachments.filter(is_deleted=False).order_by(
            "-uploaded_at"
        )
        return context


def _eligible_assets(request, statuses):
    queryset = scope_queryset(
        request.user,
        UnitAsset.objects.select_related("product", "product__brand", "current_location"),
        location_field="current_location",
    )
    queryset = queryset.filter(status__in=statuses)
    product_id = request.GET.get("product")
    if product_id:
        queryset = queryset.filter(product_id=product_id)
    return queryset.order_by("product__brand__name", "product__model")


def _quantity_lines_from_form(data, *, location_field="quantity_location"):
    if not data.get("quantity_product"):
        return []
    return [
        {
            "product": data["quantity_product"],
            "location": data[location_field],
            "quantity": data["quantity_amount"],
        }
    ]


class TransferView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/transfer_form.html"

    def get(self, request):
        form = TransferForm(user=request.user)
        assets = _eligible_assets(request, [UnitStatus.IN_STOCK, UnitStatus.RESERVED])
        return render(request, self.template_name, {"form": form, "assets": assets})

    def post(self, request):
        form = TransferForm(request.POST, user=request.user)
        unit_asset_ids = request.POST.getlist("unit_asset_ids")
        assets = _eligible_assets(request, [UnitStatus.IN_STOCK, UnitStatus.RESERVED])
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "assets": assets})

        data = form.cleaned_data
        quantity_lines = []
        if data["quantity_product"]:
            quantity_lines.append(
                {
                    "product": data["quantity_product"],
                    "source_location": data["quantity_source_location"],
                    "quantity": data["quantity_amount"],
                }
            )

        try:
            txn = bulk_transfer(
                user=request.user,
                destination_location=data["destination_location"],
                occurred_at=data["occurred_at"],
                unit_asset_ids=unit_asset_ids,
                quantity_lines=quantity_lines,
                notes=data["notes"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form, "assets": assets})

        messages.success(request, f"Transferred stock — transaction {txn.transaction_number}.")
        return redirect(txn.get_absolute_url())


class ReserveView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/reserve_form.html"

    def get(self, request):
        form = ReserveForm(user=request.user)
        assets = _eligible_assets(request, [UnitStatus.IN_STOCK])
        return render(request, self.template_name, {"form": form, "assets": assets})

    def post(self, request):
        form = ReserveForm(request.POST, user=request.user)
        unit_asset_ids = request.POST.getlist("unit_asset_ids")
        assets = _eligible_assets(request, [UnitStatus.IN_STOCK])
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "assets": assets})

        data = form.cleaned_data
        try:
            txn = reserve_stock(
                user=request.user,
                occurred_at=data["occurred_at"],
                project_reference=data["project_reference"],
                final_customer=data["final_customer"],
                unit_asset_ids=unit_asset_ids,
                quantity_lines=_quantity_lines_from_form(data),
                notes=data["notes"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form, "assets": assets})

        messages.success(request, f"Reserved stock — transaction {txn.transaction_number}.")
        return redirect(txn.get_absolute_url())


class ReservationListView(LoginRequiredMixin, ListView):
    model = StockReservation
    template_name = "inventory/reservation_list.html"
    context_object_name = "reservations"
    paginate_by = 50

    def get_queryset(self):
        queryset = scope_queryset(
            self.request.user,
            StockReservation.objects.select_related("product", "product__brand", "location"),
            location_field="location",
        )
        if self.request.GET.get("show_all") != "1":
            queryset = queryset.filter(status=ReservationStatus.ACTIVE)
        return queryset.order_by("-created_at")


class ReservationDetailView(LoginRequiredMixin, DetailView):
    model = StockReservation
    template_name = "inventory/reservation_detail.html"
    context_object_name = "reservation"

    def get_object(self, queryset=None):
        obj = get_object_or_404(
            StockReservation.objects.select_related("product", "location"), pk=self.kwargs["pk"]
        )
        require_location_access(self.request.user, obj.location)
        return obj


class ReleaseReservationView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def post(self, request, pk):
        reservation = get_object_or_404(StockReservation, pk=pk)
        try:
            release_reservation(
                user=request.user,
                occurred_at=reservation.created_at.date(),
                reservations=[reservation],
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect(reservation.get_absolute_url())

        messages.success(request, "Reservation released.")
        return redirect("inventory:reservation_list")


class AssignView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/assign_form.html"

    def get(self, request):
        form = AssignForm(user=request.user)
        assets = _eligible_assets(request, [UnitStatus.IN_STOCK, UnitStatus.RESERVED])
        return render(request, self.template_name, {"form": form, "assets": assets})

    def post(self, request):
        form = AssignForm(request.POST, user=request.user)
        unit_asset_ids = request.POST.getlist("unit_asset_ids")
        assets = _eligible_assets(request, [UnitStatus.IN_STOCK, UnitStatus.RESERVED])
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "assets": assets})

        data = form.cleaned_data
        try:
            txn = assign_to_employee(
                user=request.user,
                employee_name=data["employee_name"],
                occurred_at=data["occurred_at"],
                unit_asset_ids=unit_asset_ids,
                quantity_lines=_quantity_lines_from_form(data),
                project_reference=data["project_reference"],
                is_temporary_assignment=data["is_temporary_assignment"],
                expected_return_date=data["expected_return_date"],
                condition=data["condition"] or None,
                accessories=data["accessories"] or None,
                notes=data["notes"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form, "assets": assets})

        messages.success(request, f"Assigned stock — transaction {txn.transaction_number}.")
        return redirect(txn.get_absolute_url())


class DeliverView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/deliver_form.html"

    def get(self, request):
        form = DeliverForm(user=request.user)
        assets = _eligible_assets(request, [UnitStatus.IN_STOCK, UnitStatus.RESERVED])
        return render(request, self.template_name, {"form": form, "assets": assets})

    def post(self, request):
        form = DeliverForm(request.POST, user=request.user)
        unit_asset_ids = request.POST.getlist("unit_asset_ids")
        assets = _eligible_assets(request, [UnitStatus.IN_STOCK, UnitStatus.RESERVED])
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "assets": assets})

        data = form.cleaned_data
        try:
            txn = deliver_to_customer(
                user=request.user,
                final_customer=data["final_customer"],
                occurred_at=data["occurred_at"],
                unit_asset_ids=unit_asset_ids,
                quantity_lines=_quantity_lines_from_form(data),
                project_reference=data["project_reference"],
                condition=data["condition"] or None,
                accessories=data["accessories"] or None,
                notes=data["notes"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form, "assets": assets})

        messages.success(request, f"Delivered stock — transaction {txn.transaction_number}.")
        return redirect(txn.get_absolute_url())


class ReturnView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Partial or complete return against one assignment/delivery
    transaction (spec §9, acceptance criterion §21.7). The quantity-product
    choices are limited to products that actually appear as a quantity line
    on the original transaction, so the form can't reference an unrelated
    product — but note the service does not track how much of an original
    quantity line has already been partially returned, so repeated partial
    quantity returns against the same transaction are not capped at the
    originally issued amount. This is a known simplification.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/return_form.html"

    def _original_transaction(self, pk):
        original = get_object_or_404(
            InventoryTransaction.objects.filter(
                movement_type__in=(MovementType.ASSIGNMENT, MovementType.DELIVERY)
            ),
            pk=pk,
        )
        require_transaction_access(self.request.user, original)
        return original

    def _outstanding_lines(self, original_transaction):
        returned_asset_ids = set(
            InventoryTransactionLine.objects.filter(
                transaction__related_transaction=original_transaction,
                transaction__movement_type=MovementType.RETURN,
                unit_asset__isnull=False,
            ).values_list("unit_asset_id", flat=True)
        )
        return original_transaction.lines.exclude(
            unit_asset_id__in=returned_asset_ids
        ).select_related("unit_asset", "product")

    def _quantity_product_choices(self, original_transaction):
        product_ids = original_transaction.lines.filter(unit_asset__isnull=True).values_list(
            "product_id", flat=True
        )
        return Product.objects.filter(pk__in=product_ids)

    def _context(self, request, original_transaction, form):
        return {
            "form": form,
            "original_transaction": original_transaction,
            "lines": self._outstanding_lines(original_transaction),
        }

    def get(self, request, pk):
        original_transaction = self._original_transaction(pk)
        form = ReturnForm(
            user=request.user,
            quantity_product_choices=self._quantity_product_choices(original_transaction),
        )
        return render(
            request, self.template_name, self._context(request, original_transaction, form)
        )

    def post(self, request, pk):
        original_transaction = self._original_transaction(pk)
        form = ReturnForm(
            request.POST,
            user=request.user,
            quantity_product_choices=self._quantity_product_choices(original_transaction),
        )
        unit_asset_ids = request.POST.getlist("unit_asset_ids")
        if not form.is_valid():
            return render(
                request, self.template_name, self._context(request, original_transaction, form)
            )

        data = form.cleaned_data
        quantity_lines = []
        if data["quantity_product"]:
            quantity_lines.append(
                {"product": data["quantity_product"], "quantity": data["quantity_amount"]}
            )

        try:
            txn = return_stock(
                user=request.user,
                original_transaction=original_transaction,
                location=data["location"],
                occurred_at=data["occurred_at"],
                unit_asset_ids=unit_asset_ids,
                quantity_lines=quantity_lines,
                condition=data["condition"] or None,
                accessories=data["accessories"] or None,
                notes=data["notes"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(
                request, self.template_name, self._context(request, original_transaction, form)
            )

        messages.success(request, f"Recorded return — transaction {txn.transaction_number}.")
        return redirect(txn.get_absolute_url())


class AssessReturnView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/assess_return_form.html"

    def get(self, request):
        form = ReturnAssessmentForm()
        assets = _eligible_assets(request, [UnitStatus.RETURNED])
        return render(request, self.template_name, {"form": form, "assets": assets})

    def post(self, request):
        form = ReturnAssessmentForm(request.POST)
        unit_asset_ids = request.POST.getlist("unit_asset_ids")
        assets = _eligible_assets(request, [UnitStatus.RETURNED])
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "assets": assets})

        data = form.cleaned_data
        try:
            txn = assess_return(
                user=request.user,
                to_status=data["to_status"],
                occurred_at=data["occurred_at"],
                unit_asset_ids=unit_asset_ids,
                notes=data["notes"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form, "assets": assets})

        messages.success(
            request, f"Assessed returned stock — transaction {txn.transaction_number}."
        )
        return redirect(txn.get_absolute_url())


class _DispositionView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/disposition_form.html"
    eligible_statuses = [
        UnitStatus.IN_STOCK,
        UnitStatus.RESERVED,
        UnitStatus.ASSIGNED,
        UnitStatus.DELIVERED,
    ]
    service = None
    verb = ""
    page_title = ""

    def get(self, request):
        form = DispositionForm(user=request.user)
        assets = _eligible_assets(request, self.eligible_statuses)
        return render(
            request,
            self.template_name,
            {"form": form, "assets": assets, "page_title": self.page_title},
        )

    def post(self, request):
        form = DispositionForm(request.POST, user=request.user)
        unit_asset_ids = request.POST.getlist("unit_asset_ids")
        assets = _eligible_assets(request, self.eligible_statuses)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "assets": assets, "page_title": self.page_title},
            )

        data = form.cleaned_data
        try:
            txn = self.service(
                user=request.user,
                occurred_at=data["occurred_at"],
                unit_asset_ids=unit_asset_ids,
                quantity_lines=_quantity_lines_from_form(data),
                notes=data["notes"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(
                request,
                self.template_name,
                {"form": form, "assets": assets, "page_title": self.page_title},
            )

        messages.success(request, f"{self.verb} — transaction {txn.transaction_number}.")
        return redirect(txn.get_absolute_url())


class MarkDamagedView(_DispositionView):
    service = staticmethod(mark_damaged)
    verb = "Marked damaged"
    page_title = "Mark damaged"


class MarkLostView(_DispositionView):
    eligible_statuses = [UnitStatus.IN_STOCK, UnitStatus.RESERVED, UnitStatus.ASSIGNED]
    service = staticmethod(mark_lost)
    verb = "Marked lost"
    page_title = "Mark lost"


class DisposeView(_DispositionView):
    eligible_statuses = [
        UnitStatus.IN_STOCK,
        UnitStatus.RESERVED,
        UnitStatus.ASSIGNED,
        UnitStatus.DAMAGED,
        UnitStatus.RETURNED,
    ]
    service = staticmethod(dispose)
    verb = "Disposed"
    page_title = "Dispose"


class AdminCorrectUnitView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "inventory/admin_correct_unit_form.html"

    def get(self, request, pk):
        asset = get_object_or_404(UnitAsset, pk=pk)
        form = AdminCorrectUnitForm(
            initial={"to_status": asset.status, "to_location": asset.current_location}
        )
        return render(request, self.template_name, {"form": form, "asset": asset})

    def post(self, request, pk):
        asset = get_object_or_404(UnitAsset, pk=pk)
        form = AdminCorrectUnitForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "asset": asset})

        data = form.cleaned_data
        try:
            correct_unit_status(
                user=request.user,
                unit_asset=asset,
                to_status=data["to_status"],
                occurred_at=data["occurred_at"],
                reason=data["reason"],
                to_location=data["to_location"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form, "asset": asset})

        messages.success(request, "Correction applied.")
        return redirect(asset.get_absolute_url())


class AdminCorrectBalanceView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "inventory/admin_correct_balance_form.html"

    def get(self, request, pk):
        balance = get_object_or_404(StockBalance, pk=pk)
        form = AdminCorrectBalanceForm(initial={"new_on_hand_quantity": balance.on_hand_quantity})
        return render(request, self.template_name, {"form": form, "balance": balance})

    def post(self, request, pk):
        balance = get_object_or_404(StockBalance, pk=pk)
        form = AdminCorrectBalanceForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "balance": balance})

        data = form.cleaned_data
        try:
            correct_balance(
                user=request.user,
                product=balance.product,
                location=balance.location,
                new_on_hand_quantity=data["new_on_hand_quantity"],
                occurred_at=data["occurred_at"],
                reason=data["reason"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form, "balance": balance})

        messages.success(request, "Correction applied.")
        return redirect(balance.get_absolute_url())


class AdminReverseTransactionView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "inventory/admin_reverse_form.html"

    def get(self, request, pk):
        original_transaction = get_object_or_404(InventoryTransaction, pk=pk)
        form = AdminReversalForm()
        return render(
            request,
            self.template_name,
            {"form": form, "original_transaction": original_transaction},
        )

    def post(self, request, pk):
        original_transaction = get_object_or_404(InventoryTransaction, pk=pk)
        form = AdminReversalForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "original_transaction": original_transaction},
            )

        data = form.cleaned_data
        try:
            txn = reverse_transaction(
                user=request.user,
                original_transaction=original_transaction,
                occurred_at=data["occurred_at"],
                reason=data["reason"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(
                request,
                self.template_name,
                {"form": form, "original_transaction": original_transaction},
            )

        messages.success(request, f"Reversed — transaction {txn.transaction_number}.")
        return redirect(txn.get_absolute_url())
