import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Case, F, IntegerField, Max, Q, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, ListView

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.catalog.models import Product
from apps.core.authorization import (
    ADMINISTRATOR,
    STOCK_MANAGER,
    RoleRequiredMixin,
    has_role,
    require_role,
)
from apps.core.csv_export import CSVExportMixin
from apps.core.sorting import SortableListMixin, apply_multi_sort, parse_multi_sort
from apps.locations.scoping import (
    accessible_locations,
    location_breadcrumb_map,
    require_location_access,
    scope_queryset,
)

from .access import require_transaction_access, scope_transaction_queryset
from .filters import filter_stock_balances, filter_unit_assets
from .forms import (
    AdminCorrectBalanceForm,
    AdminCorrectUnitForm,
    AdminReversalForm,
    AssignForm,
    DeliverForm,
    DispositionForm,
    QuickReceiveForm,
    ReceiveStockForm,
    RepairDamagedForm,
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
    SavedGridView,
    StockBalance,
    StockReservation,
    UnitAsset,
    UnitStatus,
)
from .services.assignments import assign_to_employee, deliver_to_customer
from .services.corrections import correct_balance, correct_unit_status, reverse_transaction
from .services.disposition import dispose, mark_damaged, mark_lost, return_repaired_to_stock
from .services.grid_views import (
    create_saved_grid_view,
    delete_saved_grid_view,
    list_saved_grid_views,
)
from .services.receipts import DuplicateSerialError, receive_stock, receive_stock_batch
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


class QuickReceiveView(LoginRequiredMixin, RoleRequiredMixin, View):
    """One product/location/date, many serials pasted at once — the fast
    path for "we just got a box of N identical units" (linked from the
    Assets grid's page header). Each line becomes its own receive_stock()
    call via receive_stock_batch(); the response shows a per-serial result
    (created / duplicate / error) rather than an all-or-nothing outcome, so
    one bad line doesn't cost you the rest of the batch.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/quick_receive_form.html"

    def get(self, request):
        initial = {"occurred_at": None}
        product_id = request.GET.get("product")
        if product_id:
            initial["product"] = product_id
        location_id = request.GET.get("location")
        if location_id:
            initial["location"] = location_id
        form = QuickReceiveForm(user=request.user, initial=initial)
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = QuickReceiveForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        data = form.cleaned_data
        try:
            results = receive_stock_batch(
                user=request.user,
                product=data["product"],
                location=data["location"],
                occurred_at=data["occurred_at"],
                vendor_serials=data["vendor_serials"],
                project_reference=data["project_reference"],
                final_customer=data["final_customer"],
                supplier=data["supplier"],
                invoice_number=data["invoice_number"],
                condition=data["condition"],
                accessories=data["accessories"],
                notes=data["notes"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form})

        created = sum(1 for r in results if r["status"] == "created")
        messages.success(
            request,
            f"Received {created} of {len(results)} unit(s) of "
            f"{data['product']} at {data['location']}.",
        )
        return render(
            request,
            self.template_name,
            {
                "form": QuickReceiveForm(
                    user=request.user,
                    initial={
                        "product": data["product"].pk,
                        "location": data["location"].pk,
                        "occurred_at": data["occurred_at"],
                    },
                ),
                "results": results,
            },
        )


class UnitAssetListView(LoginRequiredMixin, CSVExportMixin, SortableListMixin, ListView):
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

    # Explicit allow-list, never a raw user-supplied field path — keeps
    # ?sort= from reaching into unrelated relations. Column click order
    # matches templates/inventory/asset_list.html's <th>s.
    sort_fields = {
        "product": "product__brand__name",
        "serial": "vendor_serial",
        "status": "status",
        "location": "current_location__name",
        "arrival_date": "arrival_date",
    }
    # Unset/unrecognized ?sort=: the long-standing default — most recently
    # received first — not one of the clickable columns itself.
    default_ordering = ("-created_at",)

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
        return self.apply_sort(queryset)

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


def _positive_int(value, default):
    """Shared by every grid JSON endpoint's page/size params — never lets a
    malformed or non-positive value through instead of just falling back."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


# Explicit allow-list for the grid's multi-column sort (apps.core.sorting.
# apply_multi_sort) — a superset of UnitAssetListView.sort_fields, since the
# grid exposes more columns than the classic table's 5 clickable headers.
# "country"/"storage_room"/"shelf" aren't here: they're derived in Python
# per-row (location_breadcrumb_map(), not a DB column), so they can't be
# ORDER BY'd — the grid simply doesn't offer a sorter on those 3 columns.
ASSET_GRID_SORT_FIELDS = {
    "brand": "product__brand__name",
    "model": "product__model",
    "sku": "product__sku",
    "product_type": "product__product_type__name",
    "serial": "vendor_serial",
    "status": "status",
    "condition": "condition",
    "location": "current_location__name",
    "project_reference": "project_reference",
    "final_customer": "final_customer",
    "supplier": "supplier",
    "invoice_number": "invoice_number",
    "arrival_date": "arrival_date",
    "removal_date": "last_removal_date",
    "last_movement": "last_movement_at",
}


class UnitAssetGridDataView(LoginRequiredMixin, View):
    """JSON data source for the Excel-like grid (static/js/inventory_grid.js)
    on templates/inventory/asset_list.html. Reuses UnitAssetListView's exact
    scoping/filtering — scope_queryset() + filter_unit_assets() — so this
    view has no authorization logic of its own to get wrong; it only adds
    multi-column sort (apply_multi_sort(), single-sort's multi-column
    sibling), a derived location breadcrumb, and JSON pagination on top.

    Request contract (GET): `page` (1-based), `size` (page size, capped),
    repeated `sort=field:dir` (apps.core.sorting.parse_multi_sort — the
    grid's client-side ajaxRequestFunc builds these from Tabulator's sorter
    state), plus every filter param apps.inventory.filters.filter_unit_assets
    already supports (q, brand, model, sku, type, status, location,
    project_reference, final_customer, supplier, invoice_number, serial,
    duplicate_serial, arrival_after/before, removal_after/before) — the grid
    UI's column field names are deliberately the same names, so no
    client-side translation layer is needed for filtering either.
    """

    MAX_PAGE_SIZE = 200

    def get(self, request, *args, **kwargs):
        queryset = scope_queryset(
            request.user,
            UnitAsset.objects.select_related(
                "product", "product__brand", "product__product_type", "current_location"
            ),
            location_field="current_location",
        )
        product_id = request.GET.get("product")
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        queryset = filter_unit_assets(queryset, request.GET)
        queryset = queryset.annotate(
            last_movement_at=Max("transaction_lines__transaction__created_at")
        )
        queryset = apply_multi_sort(
            queryset,
            ASSET_GRID_SORT_FIELDS,
            parse_multi_sort(request.GET),
            default_ordering=("-created_at",),
        )

        page_number = _positive_int(request.GET.get("page"), default=1)
        page_size = min(_positive_int(request.GET.get("size"), default=50), self.MAX_PAGE_SIZE)
        paginator = Paginator(queryset, page_size)
        page = paginator.get_page(page_number)

        breadcrumbs = location_breadcrumb_map()
        can_act = has_role(request.user, ADMINISTRATOR, STOCK_MANAGER)
        rows = [self._serialize(asset, breadcrumbs, can_act) for asset in page.object_list]

        return JsonResponse(
            {"data": rows, "last_page": paginator.num_pages, "total_count": paginator.count}
        )

    @staticmethod
    def _serialize(asset, breadcrumbs, can_act):
        breadcrumb = breadcrumbs.get(asset.current_location_id, {})
        return {
            "id": str(asset.pk),
            "brand": asset.product.brand.name,
            "model": asset.product.model,
            "sku": asset.product.sku,
            "product_type": asset.product.product_type.name,
            "serial": asset.vendor_serial,
            "status": asset.status,
            "status_display": asset.get_status_display(),
            "condition": asset.condition,
            "condition_display": asset.get_condition_display(),
            "location": str(asset.current_location) if asset.current_location else "",
            "country": breadcrumb.get("country", ""),
            "storage_room": breadcrumb.get("storage_room", ""),
            "shelf": breadcrumb.get("shelf", ""),
            "project_reference": asset.project_reference,
            "final_customer": asset.final_customer,
            "supplier": asset.supplier,
            "invoice_number": asset.invoice_number,
            "arrival_date": asset.arrival_date.isoformat() if asset.arrival_date else None,
            "removal_date": (
                asset.last_removal_date.isoformat() if asset.last_removal_date else None
            ),
            "notes": asset.notes,
            "last_movement": asset.last_movement_at.isoformat() if asset.last_movement_at else None,
            "detail_url": asset.get_absolute_url(),
            "quick_actions": _quick_actions_for(asset) if can_act else [],
        }


class SavedGridViewListCreateView(LoginRequiredMixin, View):
    """GET lists this grid's own+shared saved views (apps.inventory.services.
    grid_views.list_saved_grid_views); POST creates one. Backs the grid's
    "Views" dropdown and "Save current view as…" action
    (static/js/inventory_grid.js). `grid_key` ("assets"/"balances") comes
    from the URL, not the request body — one endpoint per grid, no risk of
    a view saved for one grid_key leaking into another's dropdown.
    """

    def get(self, request, grid_key):
        views = list_saved_grid_views(user=request.user, grid_key=grid_key)
        return JsonResponse(
            {
                "views": [
                    {
                        "id": str(view.pk),
                        "name": view.name,
                        "state": view.state,
                        "is_shared": view.is_shared,
                        "is_mine": view.created_by_id == request.user.id,
                    }
                    for view in views
                ]
            }
        )

    def post(self, request, grid_key):
        try:
            payload = json.loads(request.body)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid request body."}, status=400)

        try:
            view = create_saved_grid_view(
                user=request.user,
                name=payload.get("name", ""),
                grid_key=grid_key,
                state=payload.get("state") or {},
                is_shared=bool(payload.get("is_shared")),
            )
        except ValidationError as exc:
            return JsonResponse({"error": "; ".join(exc.messages)}, status=400)

        return JsonResponse(
            {
                "id": str(view.pk),
                "name": view.name,
                "state": view.state,
                "is_shared": view.is_shared,
            }
        )


class SavedGridViewDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        view = get_object_or_404(SavedGridView, pk=pk)
        try:
            delete_saved_grid_view(view=view, user=request.user)
        except PermissionDenied:
            return JsonResponse({"error": "You can only delete your own saved views."}, status=403)
        return JsonResponse({"deleted": True})


# status -> the movement actions that status is eligible for, mirroring the
# eligible_statuses each view below filters _eligible_assets() by — used to
# decide which quick-action links templates/inventory/_asset_detail_panel.html
# and the grid's per-row menu (static/js/inventory_grid.js) actually show.
# Purely a UI convenience: the destination view re-filters by its own
# eligible_statuses regardless, so an out-of-date entry here could show a
# link that turns out to have nothing preselected, never a bypass.
ASSET_QUICK_ACTIONS_BY_STATUS = {
    UnitStatus.IN_STOCK: [
        "transfer",
        "reserve",
        "assign",
        "deliver",
        "mark_damaged",
        "mark_lost",
        "dispose",
    ],
    UnitStatus.RESERVED: ["transfer", "assign", "deliver", "mark_damaged", "mark_lost", "dispose"],
    UnitStatus.ASSIGNED: ["mark_damaged", "mark_lost", "dispose"],
    UnitStatus.DELIVERED: ["mark_damaged"],
    UnitStatus.DAMAGED: ["repair_damaged", "dispose"],
    UnitStatus.RETURNED: ["dispose"],
    UnitStatus.LOST: [],
    UnitStatus.DISPOSED: [],
}
ASSET_QUICK_ACTION_LABELS = {
    "transfer": "Transfer",
    "reserve": "Reserve",
    "assign": "Assign to employee",
    "deliver": "Deliver to customer",
    "mark_damaged": "Mark damaged",
    "mark_lost": "Mark lost",
    "dispose": "Dispose",
    "repair_damaged": "Return to stock",
}


def _quick_actions_for(asset):
    """[{url, label}] for the movement actions `asset`'s current status is
    eligible for, each URL pre-filled with this one asset via the same
    ?unit_asset_ids= mechanism the grid's bulk actions use (_preselected_ids())
    — one asset is just a bulk action with a selection of one, not a
    separate code path.
    """
    return [
        {
            "url": f"{reverse(f'inventory:{name}')}?unit_asset_ids={asset.pk}",
            "label": ASSET_QUICK_ACTION_LABELS[name],
        }
        for name in ASSET_QUICK_ACTIONS_BY_STATUS.get(asset.status, [])
    ]


# Hard allow-list, not a blocklist: only these plain descriptive text fields
# are reachable through AssetGridFieldUpdateView. quantity/status/location/
# assignment/delivery/loss/disposal are structurally impossible to reach
# through this endpoint — those changes go through the movement-workflow
# services (apps.inventory.services.*), which validate and write the
# append-only ledger; this endpoint touches only the UnitAsset row itself.
ASSET_INLINE_EDITABLE_FIELDS = {
    "notes",
    "project_reference",
    "final_customer",
    "supplier",
    "invoice_number",
}


class AssetGridFieldUpdateView(LoginRequiredMixin, View):
    """POST /inventory/assets/<pk>/grid-field/ — the grid's inline-editing
    save endpoint (static/js/inventory_grid.js's cell edited handler).
    Requires Administrator or Stock Manager (same as every other mutating
    inventory action) and location access to this specific asset. Mirrors
    apps.catalog.services.update_product's old_values/new_values audit
    pattern via apps.audit.services.record_event — a quiet field edit still
    gets a durable record of who changed what, even though (unlike a
    movement) it never touches InventoryTransaction/AssetStatusHistory.
    """

    def post(self, request, pk):
        require_role(request.user, ADMINISTRATOR, STOCK_MANAGER)
        asset = get_object_or_404(UnitAsset, pk=pk)
        require_location_access(request.user, asset.current_location)

        try:
            payload = json.loads(request.body)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid request body."}, status=400)

        field = payload.get("field")
        if field not in ASSET_INLINE_EDITABLE_FIELDS:
            return JsonResponse({"error": "That field can't be edited here."}, status=400)

        value = str(payload.get("value") or "").strip()
        old_value = getattr(asset, field)
        if value == old_value:
            return JsonResponse({"field": field, "value": value})

        setattr(asset, field, value)
        asset.updated_by = request.user
        try:
            asset.full_clean(exclude=["normalized_serial"])
        except ValidationError as exc:
            return JsonResponse({"error": "; ".join(exc.messages)}, status=400)
        asset.save(update_fields=[field, "updated_by", "updated_at"])

        record_event(
            actor=request.user,
            event_type=AuditEvent.EventType.RECORD_UPDATED,
            obj=asset,
            summary=f"Updated {field} on asset {asset}",
            old_values={field: old_value},
            new_values={field: value},
        )
        return JsonResponse({"field": field, "value": value})


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

    def get_template_names(self):
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return ["inventory/_asset_detail_panel.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["history"] = self.object.status_history.select_related(
            "transaction", "from_location", "to_location", "recorded_by"
        )
        context["quick_actions"] = _quick_actions_for(self.object)
        return context


class StockBalanceListView(LoginRequiredMixin, CSVExportMixin, SortableListMixin, ListView):
    model = StockBalance
    template_name = "inventory/balance_list.html"
    context_object_name = "balances"
    paginate_by = 50
    csv_filename = "stock_balances.csv"
    csv_headers = ["Brand", "Model", "SKU", "Type", "Location", "On Hand", "Reserved", "Available"]

    # available_quantity is a computed @property (on_hand - reserved), not a
    # DB column, so it isn't sortable without an .annotate() — on_hand and
    # reserved cover the common case.
    sort_fields = {
        "product": "product__brand__name",
        "location": "location__name",
        "on_hand": "on_hand_quantity",
        "reserved": "reserved_quantity",
    }
    default_ordering = ("product__brand__name", "product__model", "location__name")

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
        return self.apply_sort(queryset)

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


# Same allow-list pattern as ASSET_GRID_SORT_FIELDS — "available" is
# annotated below (StockBalance.available_quantity is a computed @property,
# not a DB column) so it can be sorted the same as any real field.
BALANCE_GRID_SORT_FIELDS = {
    "brand": "product__brand__name",
    "model": "product__model",
    "sku": "product__sku",
    "product_type": "product__product_type__name",
    "location": "location__name",
    "on_hand": "on_hand_quantity",
    "reserved": "reserved_quantity",
    "available": "available_quantity_annotated",
}


class StockBalanceGridDataView(LoginRequiredMixin, View):
    """JSON data source for the Excel-like grid on
    templates/inventory/balance_list.html — the Stock Balances counterpart
    to UnitAssetGridDataView, reusing the exact same scope_queryset()/
    filter_stock_balances() this app's list view already uses. A lighter
    pass than the Assets grid: StockBalance has no plain descriptive text
    fields, so there's no inline editing, and no per-row/bulk actions here
    (a balance isn't a set of individually-selectable rows the way
    UnitAssets are — reserving/transferring a *quantity* is a different,
    already-existing form (apps.inventory.views.ReserveView/TransferView),
    not a "select these specific rows" action) — just search/filter/sort/
    column layout/density/pagination/export/saved views, same as any other
    grid built on static/js/inventory_grid.js.
    """

    MAX_PAGE_SIZE = 200

    def get(self, request, *args, **kwargs):
        queryset = scope_queryset(
            request.user,
            StockBalance.objects.select_related(
                "product", "product__brand", "product__product_type", "location"
            ),
            location_field="location",
        )
        product_id = request.GET.get("product")
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        queryset = filter_stock_balances(queryset, request.GET)
        queryset = queryset.annotate(
            available_quantity_annotated=F("on_hand_quantity") - F("reserved_quantity")
        )
        queryset = apply_multi_sort(
            queryset,
            BALANCE_GRID_SORT_FIELDS,
            parse_multi_sort(request.GET),
            default_ordering=("product__brand__name", "product__model", "location__name"),
        )

        page_number = _positive_int(request.GET.get("page"), default=1)
        page_size = min(_positive_int(request.GET.get("size"), default=50), self.MAX_PAGE_SIZE)
        paginator = Paginator(queryset, page_size)
        page = paginator.get_page(page_number)

        breadcrumbs = location_breadcrumb_map()
        rows = [self._serialize(balance, breadcrumbs) for balance in page.object_list]

        return JsonResponse(
            {"data": rows, "last_page": paginator.num_pages, "total_count": paginator.count}
        )

    @staticmethod
    def _serialize(balance, breadcrumbs):
        breadcrumb = breadcrumbs.get(balance.location_id, {})
        return {
            "id": str(balance.pk),
            "brand": balance.product.brand.name,
            "model": balance.product.model,
            "sku": balance.product.sku,
            "product_type": balance.product.product_type.name,
            "location": str(balance.location) if balance.location else "",
            "country": breadcrumb.get("country", ""),
            "storage_room": breadcrumb.get("storage_room", ""),
            "shelf": breadcrumb.get("shelf", ""),
            "on_hand": balance.on_hand_quantity,
            "reserved": balance.reserved_quantity,
            "available": balance.available_quantity_annotated,
            "detail_url": balance.get_absolute_url(),
        }


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


class TransactionListView(LoginRequiredMixin, SortableListMixin, ListView):
    """The "Transactions and documents" screen (spec §14)."""

    model = InventoryTransaction
    template_name = "inventory/transaction_list.html"
    context_object_name = "transactions"
    paginate_by = 50

    sort_fields = {
        "number": "transaction_number",
        "type": "movement_type",
        "date": "occurred_at",
        "performed_by": "performed_by__username",
    }
    default_ordering = ("-occurred_at", "-transaction_number")

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
        return self.apply_sort(queryset)

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


def _preselected_ids(request):
    """UUIDs (as strings) carried on the querystring by a "Transfer selected"
    /-style bulk action from the grid (static/js/inventory_grid.js) or a
    per-row quick-action link — a plain GET, not a mutation, so it's safe to
    build a link to. templates/inventory/_asset_picker.html pre-checks the
    matching checkboxes; the operator still reviews the real, unchanged
    picker/form and must submit it themselves — this is the "review and
    confirmation step" a bulk action needs, using the exact validated
    workflow every other asset action already goes through, not a shortcut
    around it.
    """
    return set(request.GET.getlist("unit_asset_ids"))


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


def _status_param(statuses):
    """Renders an eligible_statuses list as the comma-separated string
    templates/inventory/_asset_picker.html's grid passes straight through to
    AssetPickerDataView's `statuses` param — one place that knows how to
    serialize a UnitStatus list, used by every movement view that renders
    the picker so the grid always requests exactly the same eligibility
    _eligible_assets() already computed for that view's classic fallback.
    """
    return ",".join(str(status) for status in statuses)


# Explicit allow-list for the asset picker's grid sort (apps.core.sorting.
# apply_multi_sort) — deliberately small: this is a "find and select" tool
# embedded in a movement form, not a full browsing grid.
ASSET_PICKER_SORT_FIELDS = {
    "brand": "product__brand__name",
    "model": "product__model",
    "serial": "vendor_serial",
    "status": "status",
    "location": "current_location__name",
}


class AssetPickerDataView(LoginRequiredMixin, View):
    """JSON data source for the mass-selectable grid embedded in every
    movement-workflow form via templates/inventory/_asset_picker.html
    (Transfer/Reserve/Assign/Deliver/AssessReturn/MarkDamaged/MarkLost/
    Dispose/RepairDamaged). Reuses the exact scoping _eligible_assets()
    already applies (scope_queryset() + a status filter) plus
    filter_unit_assets() for search — this view has no authorization logic
    of its own, only search/sort/pagination on top so "select many" stays
    usable when the eligible set is large (an operator delivering a batch
    of 50+ units to a customer, say).

    `statuses` (required, comma-separated UnitStatus values) always comes
    from the same _status_param() call the rendering view already used for
    its own eligible_statuses — an unrecognized/missing value just yields
    an empty result (scope_queryset() is still applied to everything else),
    never a wider one.
    """

    MAX_PAGE_SIZE = 200

    def get(self, request, *args, **kwargs):
        statuses = [s for s in request.GET.get("statuses", "").split(",") if s in UnitStatus.values]
        queryset = scope_queryset(
            request.user,
            UnitAsset.objects.select_related(
                "product", "product__brand", "product__product_type", "current_location"
            ),
            location_field="current_location",
        )
        queryset = queryset.filter(status__in=statuses) if statuses else queryset.none()
        product_id = request.GET.get("product")
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        queryset = filter_unit_assets(queryset, request.GET)

        # Preselected assets (a bulk action carried over from the Assets
        # grid, or a per-row quick action — see _preselected_ids()) sort
        # first, so they land on page 1 and the picker's JS can select them
        # on load without needing every preselected id to already be
        # visible — a pure UX nicety, unrelated to eligibility itself.
        # Comma-joined under its own `preselected` param (not repeated
        # unit_asset_ids= like the page's own querystring uses) because the
        # picker's JS resends it on every ajax request via a plain object of
        # extraFilters, not a URLSearchParams multi-value append.
        preselected = {v for v in request.GET.get("preselected", "").split(",") if v}
        default_ordering = ("product__brand__name", "product__model")
        if preselected:
            queryset = queryset.annotate(
                _preselected_rank=Case(
                    When(pk__in=preselected, then=0), default=1, output_field=IntegerField()
                )
            )
            default_ordering = ("_preselected_rank", *default_ordering)
        queryset = apply_multi_sort(
            queryset,
            ASSET_PICKER_SORT_FIELDS,
            parse_multi_sort(request.GET),
            default_ordering=default_ordering,
        )

        page_number = _positive_int(request.GET.get("page"), default=1)
        page_size = min(_positive_int(request.GET.get("size"), default=100), self.MAX_PAGE_SIZE)
        paginator = Paginator(queryset, page_size)
        page = paginator.get_page(page_number)

        breadcrumbs = location_breadcrumb_map()
        rows = [self._serialize(asset, breadcrumbs, preselected) for asset in page.object_list]
        return JsonResponse(
            {"data": rows, "last_page": paginator.num_pages, "total_count": paginator.count}
        )

    @staticmethod
    def _serialize(asset, breadcrumbs, preselected):
        breadcrumb = breadcrumbs.get(asset.current_location_id, {})
        return {
            "id": str(asset.pk),
            "brand": asset.product.brand.name,
            "model": asset.product.model,
            "product": str(asset.product),
            "serial": asset.vendor_serial,
            "status": asset.status,
            "status_display": asset.get_status_display(),
            "location": str(asset.current_location) if asset.current_location else "",
            "country": breadcrumb.get("country", ""),
            "storage_room": breadcrumb.get("storage_room", ""),
            "preselected": str(asset.pk) in preselected,
        }


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
        eligible_statuses = [UnitStatus.IN_STOCK, UnitStatus.RESERVED]
        assets = _eligible_assets(request, eligible_statuses)
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "assets": assets,
                "preselected_ids": _preselected_ids(request),
                "eligible_statuses": _status_param(eligible_statuses),
            },
        )

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
        eligible_statuses = [UnitStatus.IN_STOCK]
        assets = _eligible_assets(request, eligible_statuses)
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "assets": assets,
                "preselected_ids": _preselected_ids(request),
                "eligible_statuses": _status_param(eligible_statuses),
            },
        )

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
        eligible_statuses = [UnitStatus.IN_STOCK, UnitStatus.RESERVED]
        assets = _eligible_assets(request, eligible_statuses)
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "assets": assets,
                "preselected_ids": _preselected_ids(request),
                "eligible_statuses": _status_param(eligible_statuses),
            },
        )

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
        eligible_statuses = [UnitStatus.IN_STOCK, UnitStatus.RESERVED]
        assets = _eligible_assets(request, eligible_statuses)
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "assets": assets,
                "preselected_ids": _preselected_ids(request),
                "eligible_statuses": _status_param(eligible_statuses),
            },
        )

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
        eligible_statuses = [UnitStatus.RETURNED]
        assets = _eligible_assets(request, eligible_statuses)
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "assets": assets,
                "preselected_ids": _preselected_ids(request),
                "eligible_statuses": _status_param(eligible_statuses),
            },
        )

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
            {
                "form": form,
                "assets": assets,
                "page_title": self.page_title,
                "preselected_ids": _preselected_ids(request),
                "eligible_statuses": _status_param(self.eligible_statuses),
            },
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


class RepairDamagedView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/disposition_form.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {
                "form": RepairDamagedForm(user=request.user),
                "assets": _eligible_assets(request, [UnitStatus.DAMAGED]),
                "page_title": "Return repaired assets to stock",
                "preselected_ids": _preselected_ids(request),
                "eligible_statuses": _status_param([UnitStatus.DAMAGED]),
            },
        )

    def post(self, request):
        form = RepairDamagedForm(request.POST, user=request.user)
        assets = _eligible_assets(request, [UnitStatus.DAMAGED])
        if form.is_valid():
            try:
                txn = return_repaired_to_stock(
                    user=request.user,
                    location=form.cleaned_data["location"],
                    occurred_at=form.cleaned_data["occurred_at"],
                    unit_asset_ids=request.POST.getlist("unit_asset_ids"),
                    notes=form.cleaned_data["notes"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    f"Returned repaired assets to stock — transaction {txn.transaction_number}.",
                )
                return redirect(txn.get_absolute_url())
        return render(
            request,
            self.template_name,
            {"form": form, "assets": assets, "page_title": "Return repaired assets to stock"},
        )


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
