import json
from collections import Counter
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, F, IntegerField, Max, Q, Sum, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.catalog.models import CATEGORY_TRACKING_METHOD, ItemCategory, Product, TrackingMethod
from apps.catalog.services import DuplicateProductError, resolve_or_create_product
from apps.catalog.views import _catalog_choices, _filtered_products
from apps.core.authorization import (
    ADMINISTRATOR,
    STOCK_MANAGER,
    RoleRequiredMixin,
    has_role,
    require_role,
)
from apps.core.csv_export import CSVExportMixin
from apps.core.idempotency import claim_submission_token, new_submission_token
from apps.core.recently_viewed import record_recently_viewed
from apps.core.sorting import (
    SortableListMixin,
    apply_multi_sort,
    parse_multi_sort,
    positive_int_param,
)
from apps.locations.models import Location
from apps.locations.scoping import (
    accessible_locations,
    location_breadcrumb_map,
    require_location_access,
    scope_queryset,
)

from .access import (
    require_transaction_access,
    scope_transaction_line_queryset,
    scope_transaction_queryset,
)
from .filters import filter_stock_balances, filter_unit_assets
from .forms import (
    AdminCorrectBalanceForm,
    AdminCorrectUnitForm,
    AdminReversalForm,
    AssignForm,
    DeliverForm,
    DisposeForm,
    DispositionForm,
    InstallComponentForm,
    QuantityPurposeReclassifyForm,
    QuickReceiveForm,
    ReceiveBulkBatchForm,
    ReceiveBulkFormSet,
    ReceiveStockForm,
    RemoveComponentForm,
    RepairDamagedForm,
    ReserveForm,
    ReturnAssessmentForm,
    ReturnForm,
    TransferForm,
    UnitPurposeReclassifyForm,
)
from .models import (
    Condition,
    Customer,
    InventoryTransaction,
    InventoryTransactionLine,
    MovementType,
    ReservationStatus,
    SavedGridView,
    StockBalance,
    StockPurpose,
    StockReservation,
    UnitAsset,
    UnitStatus,
)
from .services.assignments import assign_to_employee, deliver_to_customer
from .services.components import install_component, remove_component
from .services.corrections import correct_balance, correct_unit_status, reverse_transaction
from .services.disposition import dispose, mark_damaged, mark_lost, return_repaired_to_stock
from .services.grid_views import (
    create_saved_grid_view,
    delete_saved_grid_view,
    list_saved_grid_views,
)
from .services.purpose import reclassify_quantity_purpose, reclassify_unit_purpose
from .services.receipts import (
    DuplicateSerialError,
    receive_stock,
    receive_stock_batch,
    receive_stock_bulk,
    receive_stock_units_atomic,
)
from .services.reservations import release_reservation, reserve_stock
from .services.returns import assess_return, return_stock
from .services.transfers import bulk_transfer


def _recent_transactions_for_hub(user, limit=8):
    """apps.reporting.queries.recent_transactions()'s twin, kept here rather
    than imported from there: docs/architecture/01-repository-structure.md's
    dependency table has `reporting` depend on `inventory` (it's the
    read-only layer built *over* inventory/catalog/locations), never the
    reverse — reporting already imports apps.inventory.access, so inventory
    importing back from reporting would be a real circular dependency, not
    just a style preference. The query itself is 3 lines built on
    scope_transaction_queryset (already imported below for every other view
    in this module), so duplicating it here is cheaper than restructuring
    which app owns it.
    """
    return scope_transaction_queryset(
        user, InventoryTransaction.objects.select_related("performed_by")
    ).order_by("-occurred_at", "-created_at")[:limit]


def _frequently_used_for_hub(user, limit=5):
    """The Operations hub's "Frequently used" panel: top products and
    locations by movement-line count over the last 30 days, scoped exactly
    like the "Transactions and documents" screen (scope_transaction_line_
    queryset) — a shortcut/convenience surface, not an authorization
    boundary of its own, so it reuses that existing scope check rather than
    inventing a new one. See _recent_transactions_for_hub()'s docstring for
    why this lives here rather than in apps.reporting.

    Locations count both from_location and to_location touches (a transfer
    line genuinely uses both ends), tallied in Python since it's a count
    across two FK columns on what's normally a small (30-day, scoped) row
    set — not worth a second query or a UNION.
    """
    since = timezone.localdate() - timedelta(days=30)
    lines = scope_transaction_line_queryset(
        user, InventoryTransactionLine.objects.filter(transaction__occurred_at__gte=since)
    )

    products = list(
        lines.exclude(product__isnull=True)
        .values("product_id", "product__brand__name", "product__model")
        .annotate(count=Count("id"))
        .order_by("-count")[:limit]
    )

    location_counts = Counter()
    for from_id, to_id in lines.values_list("from_location_id", "to_location_id"):
        if from_id:
            location_counts[from_id] += 1
        if to_id:
            location_counts[to_id] += 1
    top_location_ids = [location_id for location_id, _ in location_counts.most_common(limit)]
    locations_by_id = Location.objects.in_bulk(top_location_ids)

    return {
        "products": [
            {
                "id": p["product_id"],
                "label": f"{p['product__brand__name']} {p['product__model']}",
                "count": p["count"],
            }
            for p in products
        ],
        "locations": [
            {"location": locations_by_id[location_id], "count": location_counts[location_id]}
            for location_id in top_location_ids
            if location_id in locations_by_id
        ],
    }


class MovementsHubView(LoginRequiredMixin, RoleRequiredMixin, View):
    """A task-focused index of the movement workflows: Primary actions
    (Receive/Transfer/Assign/Deliver), Secondary (Reserve/Return), and
    Problems (Damaged/Lost/Dispose), plus a "Recent transactions" list and a
    "Frequently used" products/locations panel — so the top nav doesn't need
    a link per workflow and an operator doesn't have to re-search for the
    product/location they just used.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def get(self, request):
        frequently_used = _frequently_used_for_hub(request.user)
        return render(
            request,
            "inventory/movements_hub.html",
            {
                "recent_transactions": _recent_transactions_for_hub(request.user),
                "frequent_products": frequently_used["products"],
                "frequent_locations": frequently_used["locations"],
            },
        )


def _default_location_for(user):
    """One fewer click on the common paths, never a hidden choice on the
    uncommon one: if `user` only has access to a single location, that's
    obviously where they're receiving into — pre-select it. Otherwise fall
    back to the location they most recently received stock into (still
    re-checked against their *current* access, so a since-revoked location
    is never offered back). Returns None — no default, pick as before — when
    neither applies.
    """
    accessible = accessible_locations(user).filter(is_active=True)
    if accessible.count() == 1:
        return accessible.first()

    last_location_id = (
        InventoryTransaction.objects.filter(
            performed_by=user,
            movement_type=MovementType.RECEIPT,
            destination_location__isnull=False,
        )
        .order_by("-created_at")
        .values_list("destination_location_id", flat=True)
        .first()
    )
    if last_location_id:
        return accessible.filter(pk=last_location_id).first()
    return None


class ReceiveStockView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Add Stock — no pre-existing Product required. resolve_or_create_product()
    (apps.catalog.services) resolves the typed brand/model/sku/type into a
    Product before receive_stock() ever runs: silent reuse on an exact match,
    a DuplicateProductError warning + acknowledgement checkbox on a close
    match (mirrors apps.catalog.views.ProductCreateView's own handling of the
    same error), or a new Product created outright.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def get(self, request):
        initial = {
            "occurred_at": timezone.localdate(),
            "submission_token": new_submission_token(),
        }
        default_location = _default_location_for(request.user)
        if default_location is not None:
            initial["location"] = default_location.pk
        product_id = request.GET.get("product")
        if product_id:
            product = (
                Product.objects.filter(pk=product_id)
                .select_related("brand", "product_type")
                .first()
            )
            if product is not None:
                initial.update(
                    brand_name=product.brand.name,
                    model=product.model,
                    sku=product.sku,
                    product_type_name=product.product_type.name,
                    category=product.category,
                )
        form = ReceiveStockForm(user=request.user, initial=initial)
        return render(
            request, "inventory/receive_stock_form.html", {"form": form, **_catalog_choices()}
        )

    def post(self, request):
        form = ReceiveStockForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(
                request, "inventory/receive_stock_form.html", {"form": form, **_catalog_choices()}
            )

        # Step 1 of 2: show exactly what will be created and let the Stock
        # Manager confirm before anything is written — a plain re-POST of
        # every submitted field plus confirmed=true, not a second form, so
        # nothing entered can be lost or re-typed between the two steps.
        if request.POST.get("confirmed") != "true":
            data = form.cleaned_data
            return render(
                request,
                "inventory/receive_stock_review.html",
                {
                    "data": data,
                    "tracking_method": CATEGORY_TRACKING_METHOD[data["category"]],
                    "category_label": ItemCategory(data["category"]).label,
                    "stock_purpose_label": StockPurpose(data["stock_purpose"]).label,
                    "condition_label": (
                        Condition(data["condition"]).label if data["condition"] else ""
                    ),
                    "hidden_fields": [
                        (key, value)
                        for key, value in request.POST.items()
                        if key != "csrfmiddlewaretoken"
                    ],
                },
            )

        if not claim_submission_token(request.POST.get("submission_token")):
            messages.info(
                request, "This receipt was already submitted — no duplicate stock was created."
            )
            return redirect("inventory:movements_hub")

        data = form.cleaned_data
        try:
            product = resolve_or_create_product(
                user=request.user,
                brand_name=data["brand_name"],
                model=data["model"],
                sku=data["sku"],
                product_type_name=data["product_type_name"],
                category=data["category"],
                duplicate_acknowledged=request.POST.get("duplicate_acknowledged") == "true",
            )
        except DuplicateProductError as exc:
            return render(
                request,
                "inventory/receive_stock_form.html",
                {
                    "form": form,
                    "duplicate_product_matches": exc.matches,
                    "show_duplicate_product_warning": True,
                    **_catalog_choices(),
                },
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(
                request, "inventory/receive_stock_form.html", {"form": form, **_catalog_choices()}
            )

        tracking_method = CATEGORY_TRACKING_METHOD[data["category"]]
        common_fields = dict(
            location=data["location"],
            occurred_at=data["occurred_at"],
            stock_purpose=data["stock_purpose"],
            project_reference=data["project_reference"],
            final_customer=data["final_customer"],
            supplier=data["supplier"],
            invoice_number=data["invoice_number"],
            condition=data["condition"],
            accessories=data["accessories"],
            notes=data["notes"],
        )
        duplicate_serial_acknowledged = request.POST.get("duplicate_serial_acknowledged") == "true"
        try:
            if tracking_method == TrackingMethod.QUANTITY:
                txn = receive_stock(
                    user=request.user, product=product, quantity=data["quantity"], **common_fields
                )
                transactions = [txn]
                created_serials = []
            else:
                serials = data["parsed_serials"]
                unit_count = data["unit_count"]
                if unit_count <= 1:
                    vendor_serial = serials[0] if serials else ""
                    txn = receive_stock(
                        user=request.user,
                        product=product,
                        vendor_serial=vendor_serial,
                        duplicate_serial_acknowledged=duplicate_serial_acknowledged,
                        **common_fields,
                    )
                    transactions = [txn]
                    created_serials = [vendor_serial] if vendor_serial else []
                else:
                    vendor_serials_list = serials if serials else [""] * unit_count
                    transactions = receive_stock_units_atomic(
                        user=request.user,
                        product=product,
                        vendor_serials=vendor_serials_list,
                        duplicate_serial_acknowledged=duplicate_serial_acknowledged,
                        **common_fields,
                    )
                    created_serials = [s for s in vendor_serials_list if s]
        except DuplicateSerialError as exc:
            return render(
                request,
                "inventory/receive_stock_form.html",
                {
                    "form": form,
                    "duplicate_matches": exc.matches,
                    "show_duplicate_warning": True,
                    **_catalog_choices(),
                },
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(
                request, "inventory/receive_stock_form.html", {"form": form, **_catalog_choices()}
            )

        if tracking_method == TrackingMethod.QUANTITY:
            balance = StockBalance.objects.filter(
                product=product, location=data["location"], stock_purpose=data["stock_purpose"]
            ).first()
            available_quantity = balance.available_quantity if balance else data["quantity"]
            received_quantity = data["quantity"]
        else:
            available_quantity = UnitAsset.objects.filter(
                product=product, current_location=data["location"], status=UnitStatus.IN_STOCK
            ).count()
            received_quantity = len(transactions)

        messages.success(
            request,
            f"Received {received_quantity} × {product} — "
            f"transaction {transactions[0].transaction_number}"
            + (f" (+{len(transactions) - 1} more)" if len(transactions) > 1 else "")
            + ".",
        )
        return render(
            request,
            "inventory/receive_stock_summary.html",
            {
                "product": product,
                "location": data["location"],
                "received_quantity": received_quantity,
                "serials": created_serials,
                "available_quantity": available_quantity,
                "transactions": transactions,
            },
        )


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
        initial = {"occurred_at": timezone.localdate()}
        product_id = request.GET.get("product")
        if product_id:
            initial["product"] = product_id
        location_id = request.GET.get("location")
        if location_id:
            initial["location"] = location_id
        else:
            default_location = _default_location_for(request.user)
            if default_location is not None:
                initial["location"] = default_location.pk
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
                stock_purpose=data["stock_purpose"],
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
                        "stock_purpose": data["stock_purpose"],
                    },
                ),
                "results": results,
            },
        )


class ReceiveBulkView(LoginRequiredMixin, RoleRequiredMixin, View):
    """A single atomic multi-line goods receipt — several products, mixed
    serialized/quantity, one shared default location/purpose with a per-row
    override, committed as one InventoryTransaction via receive_stock_bulk()
    (unlike QuickReceiveView's receive_stock_batch(), which is explicitly
    not atomic across rows). GET renders the batch form + line formset. A
    POST that fails validation re-renders with errors; a valid POST calls
    the service directly — the rendered formset (still showing every
    entered value, plus a summary block the template renders above the
    submit button) doubles as the review step, per the existing pattern
    every other movement form in this app already uses for surfacing
    validation problems before commit.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/receive_bulk_form.html"

    def get(self, request):
        initial = {"occurred_at": timezone.localdate()}
        default_location = _default_location_for(request.user)
        if default_location is not None:
            initial["default_location"] = default_location.pk
        batch_form = ReceiveBulkBatchForm(user=request.user, initial=initial)
        formset = ReceiveBulkFormSet(form_kwargs={"user": request.user})
        return render(
            request,
            self.template_name,
            {"batch_form": batch_form, "formset": formset, **_catalog_choices()},
        )

    def post(self, request):
        batch_form = ReceiveBulkBatchForm(request.POST, user=request.user)
        formset = ReceiveBulkFormSet(request.POST, form_kwargs={"user": request.user})
        batch_valid = batch_form.is_valid()
        formset_valid = formset.is_valid()
        if not (batch_valid and formset_valid):
            return render(
                request,
                self.template_name,
                {"batch_form": batch_form, "formset": formset, **_catalog_choices()},
            )

        rows = [
            row
            for row in formset.cleaned_data
            if row and (row.get("brand_name") or row.get("model"))
        ]
        try:
            with transaction.atomic():
                resolved = [
                    (
                        row,
                        resolve_or_create_product(
                            user=request.user,
                            brand_name=row["brand_name"],
                            model=row["model"],
                            sku=row.get("sku", ""),
                            product_type_name=row["product_type_name"],
                            category=row["category"],
                            duplicate_acknowledged=request.POST.get("duplicate_acknowledged")
                            == "true",
                        ),
                    )
                    for row in rows
                ]
        except DuplicateProductError as exc:
            return render(
                request,
                self.template_name,
                {
                    "batch_form": batch_form,
                    "formset": formset,
                    "duplicate_product_matches": exc.matches,
                    "show_duplicate_product_warning": True,
                    **_catalog_choices(),
                },
            )

        line_rows = []
        for row, product in resolved:
            entry = {
                "product": product,
                "location": row.get("location") or None,
                "stock_purpose": row.get("stock_purpose") or None,
                "notes": row.get("notes", ""),
            }
            if product.tracking_method == TrackingMethod.UNIT:
                entry["vendor_serials"] = row.get("parsed_serials", [])
                entry["condition"] = row.get("condition")
                entry["accessories"] = row.get("accessories", "")
                entry["arrival_date"] = row.get("arrival_date_override") or None
            else:
                entry["quantity"] = row.get("quantity")
            line_rows.append(entry)

        if not line_rows:
            batch_form.add_error(None, "Add at least one line to the receipt.")
            return render(
                request,
                self.template_name,
                {"batch_form": batch_form, "formset": formset, **_catalog_choices()},
            )

        data = batch_form.cleaned_data
        try:
            txn = receive_stock_bulk(
                user=request.user,
                occurred_at=data["occurred_at"],
                default_location=data["default_location"],
                default_stock_purpose=data["default_stock_purpose"],
                lines=line_rows,
                supplier=data["supplier"],
                invoice_number=data["invoice_number"],
                project_reference=data["project_reference"],
                final_customer=data["final_customer"],
                notes=data["notes"],
                duplicate_serial_acknowledged=request.POST.get("duplicate_serial_acknowledged")
                == "true",
            )
        except DuplicateSerialError as exc:
            return render(
                request,
                self.template_name,
                {
                    "batch_form": batch_form,
                    "formset": formset,
                    "duplicate_matches": exc.matches,
                    "duplicate_by_serial": exc.by_serial,
                    "show_duplicate_warning": True,
                    **_catalog_choices(),
                },
            )
        except ValidationError as exc:
            batch_form.add_error(None, exc)
            return render(
                request,
                self.template_name,
                {"batch_form": batch_form, "formset": formset, **_catalog_choices()},
            )

        messages.success(
            request,
            f"Received {len(line_rows)} line(s) into stock — transaction "
            f"{txn.transaction_number}.",
        )
        return redirect(txn.get_absolute_url())


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
        "Stock Purpose",
        "Location",
        "Assigned To",
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
                "product",
                "product__brand",
                "product__product_type",
                "current_location",
                "current_custody_transaction",
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
            assigned_to = _assigned_to_block(asset.current_custody_transaction)
            yield [
                asset.product.brand.name,
                asset.product.model,
                asset.product.sku,
                asset.product.product_type.name,
                asset.vendor_serial,
                asset.get_status_display(),
                asset.get_stock_purpose_display(),
                str(asset.current_location or ""),
                assigned_to["name"] if assigned_to else "",
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
        context["stock_purposes"] = StockPurpose.choices
        context["locations"] = accessible_locations(self.request.user).order_by("level", "name")
        context["filters"] = self.request.GET
        return context


# positive_int_param (apps.core.sorting) covers page/size parsing for this
# and every other grid JSON endpoint — see its own docstring.
_positive_int = positive_int_param


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
                "product",
                "product__brand",
                "product__product_type",
                "current_location",
                "current_custody_transaction",
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
            "stock_purpose": asset.stock_purpose,
            "stock_purpose_display": asset.get_stock_purpose_display(),
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
            "assigned_to": _assigned_to_block(asset.current_custody_transaction),
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


def _assigned_to_block(custody_transaction):
    """The "Assigned To" display block for a UnitAsset's current custody
    pointer (models.py's UnitAsset.current_custody_transaction) — every
    field is read from that one transaction rather than duplicated onto
    UnitAsset, so this is the single place that shape gets assembled for
    the grid, the asset detail page, and search results. None when the
    asset isn't currently assigned/delivered.
    """
    if custody_transaction is None:
        return None
    is_assignment = custody_transaction.movement_type == MovementType.ASSIGNMENT
    return {
        "type": "employee" if is_assignment else "customer",
        "type_display": "Employee" if is_assignment else "Customer",
        "name": custody_transaction.employee_name or custody_transaction.final_customer,
        "reference": custody_transaction.recipient_reference,
        "project_reference": custody_transaction.project_reference,
        "transaction_id": str(custody_transaction.pk),
        "transaction_number": custody_transaction.transaction_number,
        "transaction_url": custody_transaction.get_absolute_url(),
        "date": custody_transaction.occurred_at.isoformat(),
        "expected_return_date": (
            custody_transaction.expected_return_date.isoformat()
            if custody_transaction.expected_return_date
            else None
        ),
        "notes": custody_transaction.notes,
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
            UnitAsset.objects.select_related(
                "product",
                "product__brand",
                "current_location",
                "current_custody_transaction",
                "installed_in",
                "installed_in__product",
                "installed_in__product__brand",
            ),
            pk=self.kwargs["pk"],
        )
        require_location_access(self.request.user, obj.current_location)
        return obj

    def get_template_names(self):
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return ["inventory/_asset_detail_panel.html"]
        return [self.template_name]

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        # Only the real page view, never the grid's AJAX side-panel fetch —
        # see apps.core.recently_viewed.record_recently_viewed()'s docstring.
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            record_recently_viewed(user=request.user, obj=self.object)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["history"] = self.object.status_history.select_related(
            "transaction", "from_location", "to_location", "recorded_by"
        )
        context["quick_actions"] = _quick_actions_for(self.object)
        context["assigned_to"] = _assigned_to_block(self.object.current_custody_transaction)
        context["other_stock_purpose"] = (
            StockPurpose.CUSTOMER
            if self.object.stock_purpose == StockPurpose.INTERNAL
            else StockPurpose.INTERNAL
        )
        # Component install/remove — only Component-category items can be
        # installed into a parent, but any asset can host components, so
        # "installed_components" is always computed (cheap: at most a
        # handful of rows), not gated on this asset's own category.
        context["can_install_component"] = (
            self.object.product.category == ItemCategory.COMPONENT
            and self.object.installed_in_id is None
            and self.object.status == UnitStatus.IN_STOCK
        )
        context["installed_components"] = self.object.installed_components.select_related(
            "product", "product__brand"
        ).order_by("product__brand__name", "product__model")
        return context


class InstallComponentView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Reached from a Component-category asset's own detail page — install
    it into a parent asset the operator picks. See
    apps.inventory.services.components for the eligibility rules.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/install_component_form.html"

    def _component(self, request, pk):
        component = get_object_or_404(
            UnitAsset.objects.select_related("product", "product__brand"), pk=pk
        )
        require_location_access(request.user, component.current_location)
        return component

    def get(self, request, pk):
        component = self._component(request, pk)
        form = InstallComponentForm(user=request.user, component=component)
        return render(request, self.template_name, {"form": form, "component": component})

    def post(self, request, pk):
        component = self._component(request, pk)
        form = InstallComponentForm(request.POST, user=request.user, component=component)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "component": component})

        data = form.cleaned_data
        try:
            txn = install_component(
                user=request.user,
                component_id=component.pk,
                parent_id=data["parent_asset"].pk,
                occurred_at=data["occurred_at"],
                notes=data["notes"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form, "component": component})

        messages.success(
            request,
            f"Installed {component} into {data['parent_asset']} — "
            f"transaction {txn.transaction_number}.",
        )
        return redirect(component.get_absolute_url())


class RemoveComponentView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Reached from either the component's own detail page ("Installed in:
    ... [Remove]") or the parent's detail page's installed-components list.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/remove_component_form.html"

    def _component(self, request, pk):
        component = get_object_or_404(
            UnitAsset.objects.select_related(
                "product", "product__brand", "installed_in", "installed_in__product"
            ),
            pk=pk,
        )
        require_location_access(request.user, component.current_location)
        return component

    def get(self, request, pk):
        component = self._component(request, pk)
        form = RemoveComponentForm(initial={"occurred_at": timezone.localdate()})
        return render(request, self.template_name, {"form": form, "component": component})

    def post(self, request, pk):
        component = self._component(request, pk)
        form = RemoveComponentForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "component": component})

        data = form.cleaned_data
        try:
            txn = remove_component(
                user=request.user,
                component_id=component.pk,
                occurred_at=data["occurred_at"],
                notes=data["notes"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form, "component": component})

        messages.success(request, f"Removed {component} — transaction {txn.transaction_number}.")
        return redirect(component.get_absolute_url())


class StockBalanceListView(LoginRequiredMixin, CSVExportMixin, SortableListMixin, ListView):
    model = StockBalance
    template_name = "inventory/balance_list.html"
    context_object_name = "balances"
    paginate_by = 50
    csv_filename = "stock_balances.csv"
    csv_headers = [
        "Brand",
        "Model",
        "SKU",
        "Type",
        "Location",
        "Stock Purpose",
        "On Hand",
        "Reserved",
        "Available",
    ]

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
                balance.get_stock_purpose_display(),
                balance.on_hand_quantity,
                balance.reserved_quantity,
                balance.available_quantity,
            ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["locations"] = accessible_locations(self.request.user).order_by("level", "name")
        context["stock_purposes"] = StockPurpose.choices
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
            "stock_purpose": balance.stock_purpose,
            "stock_purpose_display": balance.get_stock_purpose_display(),
            "on_hand": balance.on_hand_quantity,
            "reserved": balance.reserved_quantity,
            "available": balance.available_quantity_annotated,
            "detail_url": balance.get_absolute_url(),
        }


# Explicit allow-list for the Products grid's multi-column sort, same
# pattern as ASSET_GRID_SORT_FIELDS/BALANCE_GRID_SORT_FIELDS.
PRODUCT_GRID_SORT_FIELDS = {
    "brand": "brand__name",
    "model": "model",
    "sku": "sku",
    "product_type": "product_type__name",
    "category": "category",
    "tracking_method": "tracking_method",
    "supplier": "supplier",
    "low_stock_threshold": "low_stock_threshold",
    "status": "is_active",
}


def _scoped_available_by_product(user):
    """product_id -> available quantity (on_hand - reserved), summed across
    only this user's accessible locations — mirrors apps.reporting.queries.
    low_stock_balances()'s own scoping and arithmetic exactly, so the grid's
    "Low stock" badge and that report can never disagree. Products with no
    StockBalance row anywhere in scope simply have no key here (never a
    false "0 available").
    """
    return dict(
        scope_queryset(user, StockBalance.objects.all(), location_field="location")
        .values("product_id")
        .annotate(available=Sum(F("on_hand_quantity") - F("reserved_quantity")))
        .values_list("product_id", "available")
    )


class ProductGridDataView(LoginRequiredMixin, View):
    """JSON data source for the Excel-like grid on
    templates/catalog/product_list.html — the Products counterpart to
    UnitAssetGridDataView/StockBalanceGridDataView, living here rather than
    in apps.catalog because it needs StockBalance (an inventory concept) to
    compute the "Low stock" badge; docs/architecture/01-repository-structure.
    md's dependency table has catalog depend on nothing but core, so this
    view — not a model or service import — is the boundary-respecting side
    to put the cross-app read on (apps.catalog.views._filtered_products() is
    the only thing borrowed from catalog, reused exactly as ProductListView
    itself uses it).

    Unlike the Assets/Balances grids, Products themselves are catalog-global
    (no location field), so there's no scope_queryset() on the base
    queryset — only the per-product available-quantity figure used for the
    low-stock badge is location-scoped (_scoped_available_by_product()), so
    a Stock Manager sees that badge computed from their own accessible
    locations' balances, never a global total, while still seeing every
    product itself (same as the classic Products table today).
    """

    MAX_PAGE_SIZE = 200

    def get(self, request, *args, **kwargs):
        queryset = _filtered_products(request).select_related("brand", "product_type")
        queryset = apply_multi_sort(
            queryset,
            PRODUCT_GRID_SORT_FIELDS,
            parse_multi_sort(request.GET),
            default_ordering=("brand__name", "model"),
        )

        page_number = _positive_int(request.GET.get("page"), default=1)
        page_size = min(_positive_int(request.GET.get("size"), default=50), self.MAX_PAGE_SIZE)
        paginator = Paginator(queryset, page_size)
        page = paginator.get_page(page_number)

        available_by_product = _scoped_available_by_product(request.user)
        rows = [self._serialize(product, available_by_product) for product in page.object_list]

        return JsonResponse(
            {"data": rows, "last_page": paginator.num_pages, "total_count": paginator.count}
        )

    @staticmethod
    def _serialize(product, available_by_product):
        available = available_by_product.get(product.pk)
        is_low_stock = (
            product.tracking_method == TrackingMethod.QUANTITY
            and product.low_stock_threshold is not None
            and available is not None
            and available <= product.low_stock_threshold
        )
        return {
            "id": str(product.pk),
            "brand": product.brand.name,
            "model": product.model,
            "sku": product.sku,
            "product_type": product.product_type.name,
            "category": product.category or "",
            "category_display": product.get_category_display() if product.category else "",
            "tracking_method": product.tracking_method,
            "tracking_method_display": product.get_tracking_method_display(),
            "supplier": product.supplier,
            "low_stock_threshold": product.low_stock_threshold,
            "available": available,
            "is_low_stock": is_low_stock,
            "status": "active" if product.is_active else "inactive",
            "status_display": "Active" if product.is_active else "Inactive",
            "detail_url": product.get_absolute_url(),
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

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        record_recently_viewed(user=request.user, obj=self.object)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["lines"] = self.object.lines.select_related("unit_asset", "product").order_by(
            "line_number"
        )
        context["can_return"] = self.object.movement_type in (
            MovementType.ASSIGNMENT,
            MovementType.DELIVERY,
        )
        # Separate from can_return: a disposal is terminal (never returned —
        # UnitStatus.DISPOSED has no eligible next actions, see
        # ASSET_QUICK_ACTIONS_BY_STATUS above) but still generates a
        # printable document (its certificate), so the Documents section
        # needs its own, wider flag rather than reusing can_return.
        context["can_generate_document"] = self.object.movement_type in (
            MovementType.ASSIGNMENT,
            MovementType.DELIVERY,
            MovementType.DISPOSAL,
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


def _eligible_balances(request):
    """StockBalance rows with stock actually available to issue — the
    quantity-tracked counterpart to _eligible_assets(). Zero-available rows
    are excluded outright rather than shown-but-uncheckable: nothing useful
    comes from letting an operator pick a row it's impossible to draw from.
    """
    queryset = scope_queryset(
        request.user,
        StockBalance.objects.select_related("product", "product__brand", "location"),
        location_field="location",
    )
    queryset = queryset.annotate(
        available_quantity_annotated=F("on_hand_quantity") - F("reserved_quantity")
    ).filter(available_quantity_annotated__gt=0)
    product_id = request.GET.get("product")
    if product_id:
        queryset = queryset.filter(product_id=product_id)
    return queryset.order_by("product__brand__name", "product__model")


BALANCE_PICKER_SORT_FIELDS = {
    "brand": "product__brand__name",
    "model": "product__model",
    "location": "location__name",
    "stock_purpose": "stock_purpose",
    "available": "available_quantity_annotated",
}


class BalancePickerDataView(LoginRequiredMixin, View):
    """JSON data source for the mass-selectable quantity-row grid embedded
    in Assign/Deliver (templates/inventory/_balance_picker.html) — the
    quantity-tracked counterpart to AssetPickerDataView. Each row is one
    specific (product, location, stock_purpose) StockBalance with available
    stock; picking a row (and entering how much to take from it) is how the
    Stock Manager chooses what to issue, without ever seeing a separate
    product/location/purpose dropdown — see AssignForm's docstring and
    apps.inventory.views._quantity_lines_from_balance_picker().
    """

    MAX_PAGE_SIZE = 200

    def get(self, request, *args, **kwargs):
        queryset = filter_stock_balances(_eligible_balances(request), request.GET)
        queryset = apply_multi_sort(
            queryset,
            BALANCE_PICKER_SORT_FIELDS,
            parse_multi_sort(request.GET),
            default_ordering=("product__brand__name", "product__model"),
        )

        page_number = _positive_int(request.GET.get("page"), default=1)
        page_size = min(_positive_int(request.GET.get("size"), default=100), self.MAX_PAGE_SIZE)
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
            "product": str(balance.product),
            "location": str(balance.location),
            "country": breadcrumb.get("country", ""),
            "storage_room": breadcrumb.get("storage_room", ""),
            "stock_purpose": balance.stock_purpose,
            "stock_purpose_display": balance.get_stock_purpose_display(),
            "available": balance.available_quantity_annotated,
        }


def _quantity_lines_from_form(data, *, location_field="quantity_location"):
    if not data.get("quantity_product"):
        return []
    return [
        {
            "product": data["quantity_product"],
            "location": data[location_field],
            "quantity": data["quantity_amount"],
            "stock_purpose": data.get("quantity_stock_purpose") or StockPurpose.INTERNAL,
        }
    ]


def _quantity_lines_from_balance_picker(request, user):
    """Assign/Deliver's quantity-tracked lines: templates/inventory/
    _balance_picker.html (static/js/movement_forms.js's wireBalancePicker())
    posts one JSON array in `quantity_lines_json`, each entry naming a
    specific StockBalance row plus the quantity to take from it — so
    product/location/stock_purpose are always implicit in *which row* the
    Stock Manager picked, never a separate dropdown (per direct
    instruction). Raises ValidationError (caught by the view exactly like
    every other movement-form error) rather than trusting the client's
    numbers past the row's own available_quantity.
    """
    raw = request.POST.get("quantity_lines_json", "")
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Invalid quantity selection.") from exc
    if not isinstance(entries, list):
        raise ValidationError("Invalid quantity selection.")

    balance_ids = [entry.get("balance_id") for entry in entries if entry.get("balance_id")]
    balances = {
        str(balance.pk): balance
        for balance in StockBalance.objects.select_related("product", "location").filter(
            pk__in=balance_ids
        )
    }

    lines = []
    for entry in entries:
        balance = balances.get(str(entry.get("balance_id")))
        if balance is None:
            raise ValidationError("One or more selected quantity rows could not be found.")
        try:
            quantity = int(entry.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        if quantity <= 0:
            continue
        require_location_access(user, balance.location)
        if quantity > balance.available_quantity:
            raise ValidationError(
                f"Only {balance.available_quantity} available for {balance.product} at "
                f"{balance.location} ({balance.get_stock_purpose_display()}) — "
                f"{quantity} requested."
            )
        lines.append(
            {
                "product": balance.product,
                "location": balance.location,
                "quantity": quantity,
                "stock_purpose": balance.stock_purpose,
            }
        )
    return lines


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
                    "stock_purpose": data.get("quantity_stock_purpose") or StockPurpose.INTERNAL,
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
                "balances": _eligible_balances(request),
                "preselected_ids": _preselected_ids(request),
                "eligible_statuses": _status_param(eligible_statuses),
            },
        )

    def post(self, request):
        form = AssignForm(request.POST, user=request.user)
        unit_asset_ids = request.POST.getlist("unit_asset_ids")
        assets = _eligible_assets(request, [UnitStatus.IN_STOCK, UnitStatus.RESERVED])
        balances = _eligible_balances(request)
        if not form.is_valid():
            return render(
                request, self.template_name, {"form": form, "assets": assets, "balances": balances}
            )

        data = form.cleaned_data
        try:
            quantity_lines = _quantity_lines_from_balance_picker(request, request.user)
            txn = assign_to_employee(
                user=request.user,
                employee_name=data["employee_name"],
                occurred_at=data["occurred_at"],
                unit_asset_ids=unit_asset_ids,
                quantity_lines=quantity_lines,
                project_reference=data["project_reference"],
                recipient_reference=data["recipient_reference"],
                is_temporary_assignment=data["is_temporary_assignment"],
                expected_return_date=data["expected_return_date"],
                condition=data["condition"] or None,
                accessories=data["accessories"] or None,
                notes=data["notes"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(
                request, self.template_name, {"form": form, "assets": assets, "balances": balances}
            )

        messages.success(request, f"Assigned stock — transaction {txn.transaction_number}.")
        return redirect(txn.get_absolute_url())


CUSTOMER_SEARCH_LIMIT = 20


def _customer_search_results(user, query=""):
    """Ranks real Customer rows first, then falls back to distinct historical
    InventoryTransaction.final_customer text, so a customer that was only
    ever typed as free text (never formally registered — spec §22 excludes
    customer master-data management) stays findable. Shared by
    CustomerSearchDataView (live search) and DeliverView (the datalist's
    initial options at page load).
    """
    results = []
    seen = set()

    customers = Customer.objects.filter(is_active=True)
    if query:
        customers = customers.filter(Q(name__icontains=query) | Q(reference__icontains=query))
    for customer in customers.order_by("name")[:CUSTOMER_SEARCH_LIMIT]:
        key = customer.name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "id": str(customer.pk),
                "name": customer.name,
                "reference": customer.reference,
                "source": "customer",
            }
        )

    if len(results) < CUSTOMER_SEARCH_LIMIT:
        historical = scope_transaction_queryset(
            user, InventoryTransaction.objects.exclude(final_customer="")
        ).order_by()
        if query:
            historical = historical.filter(final_customer__icontains=query)
        names = historical.values_list("final_customer", flat=True).distinct()[
            : CUSTOMER_SEARCH_LIMIT * 2
        ]
        for name in names:
            key = name.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            results.append({"id": "", "name": name, "reference": "", "source": "history"})
            if len(results) >= CUSTOMER_SEARCH_LIMIT:
                break

    return results


class CustomerSearchDataView(LoginRequiredMixin, View):
    """JSON search backing DeliverForm's final_customer autocomplete (and,
    per the plan, the future multi-item delivery grid). See
    _customer_search_results() for the ranking/fallback rule.
    """

    def get(self, request, *args, **kwargs):
        query = request.GET.get("q", "").strip()
        return JsonResponse({"results": _customer_search_results(request.user, query)})


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
                "balances": _eligible_balances(request),
                "preselected_ids": _preselected_ids(request),
                "eligible_statuses": _status_param(eligible_statuses),
                "customer_choices": _customer_search_results(request.user),
            },
        )

    def post(self, request):
        form = DeliverForm(request.POST, user=request.user)
        unit_asset_ids = request.POST.getlist("unit_asset_ids")
        assets = _eligible_assets(request, [UnitStatus.IN_STOCK, UnitStatus.RESERVED])
        balances = _eligible_balances(request)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "assets": assets,
                    "balances": balances,
                    "customer_choices": _customer_search_results(request.user),
                },
            )

        data = form.cleaned_data
        try:
            quantity_lines = _quantity_lines_from_balance_picker(request, request.user)
            txn = deliver_to_customer(
                user=request.user,
                final_customer=data["final_customer"],
                customer=data["customer"],
                occurred_at=data["occurred_at"],
                unit_asset_ids=unit_asset_ids,
                quantity_lines=quantity_lines,
                project_reference=data["project_reference"],
                recipient_reference=data["recipient_reference"],
                condition=data["condition"] or None,
                accessories=data["accessories"] or None,
                notes=data["notes"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "assets": assets,
                    "balances": balances,
                    "customer_choices": _customer_search_results(request.user),
                },
            )

        messages.success(request, f"Delivered stock — transaction {txn.transaction_number}.")
        return redirect(txn.get_absolute_url())


class ReturnView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Partial or complete return against one assignment/delivery
    transaction (spec §9, acceptance criterion §21.7). The quantity-product
    choices are limited to products that actually appear as a quantity line
    on the original transaction, so the form can't reference an unrelated
    product; the service (apps.inventory.services.returns.return_stock())
    also tracks how much of an original quantity line has already been
    returned and caps a return at the outstanding amount, so repeated
    partial returns against the same transaction can't over-return.
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
                {
                    "product": data["quantity_product"],
                    "quantity": data["quantity_amount"],
                    "stock_purpose": data.get("quantity_stock_purpose") or StockPurpose.INTERNAL,
                }
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
    form_class = DispositionForm
    eligible_statuses = [
        UnitStatus.IN_STOCK,
        UnitStatus.RESERVED,
        UnitStatus.ASSIGNED,
        UnitStatus.DELIVERED,
    ]
    service = None
    verb = ""
    page_title = ""

    def _extra_service_kwargs(self, data):
        """Hook for a subclass whose form_class carries fields beyond the
        shared DispositionForm's (see DisposeView/DisposeForm) — nothing to
        add for Mark damaged/Mark lost."""
        return {}

    def get(self, request):
        form = self.form_class(user=request.user)
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
        form = self.form_class(request.POST, user=request.user)
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
                **self._extra_service_kwargs(data),
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
    form_class = DisposeForm

    def _extra_service_kwargs(self, data):
        return {"wipe_method": data["wipe_method"], "witness_name": data["witness_name"]}


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
                arrival_date=data["arrival_date"],
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
                stock_purpose=balance.stock_purpose,
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


class UnitPurposeReclassifyView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Relabels one serialized asset's Stock Purpose — a label change, not a
    movement, so unlike every other action on this page there's no eligible-
    statuses gate: an asset can be reclassified in any status.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/purpose_reclassify_unit_form.html"

    def get(self, request, pk):
        asset = get_object_or_404(UnitAsset, pk=pk)
        require_location_access(request.user, asset.current_location)
        new_purpose = (
            StockPurpose.CUSTOMER
            if asset.stock_purpose == StockPurpose.INTERNAL
            else StockPurpose.INTERNAL
        )
        form = UnitPurposeReclassifyForm(initial={"new_purpose": new_purpose})
        return render(request, self.template_name, {"form": form, "asset": asset})

    def post(self, request, pk):
        asset = get_object_or_404(UnitAsset, pk=pk)
        require_location_access(request.user, asset.current_location)
        form = UnitPurposeReclassifyForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "asset": asset})

        data = form.cleaned_data
        try:
            reclassify_unit_purpose(
                user=request.user,
                unit_asset=asset,
                new_purpose=data["new_purpose"],
                occurred_at=data["occurred_at"],
                reason=data["reason"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form, "asset": asset})

        messages.success(request, "Stock purpose updated.")
        return redirect(asset.get_absolute_url())


class QuantityPurposeReclassifyView(LoginRequiredMixin, RoleRequiredMixin, View):
    """Moves quantity between the Internal and Customer buckets of one
    StockBalance row at the same location (reclassify_quantity_purpose()) —
    a real ledger transaction, unlike the unit-asset case.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "inventory/purpose_reclassify_quantity_form.html"

    def get(self, request, pk):
        balance = get_object_or_404(StockBalance, pk=pk)
        require_location_access(request.user, balance.location)
        other_purpose = (
            StockPurpose.CUSTOMER
            if balance.stock_purpose == StockPurpose.INTERNAL
            else StockPurpose.INTERNAL
        )
        form = QuantityPurposeReclassifyForm(
            initial={"from_purpose": balance.stock_purpose, "to_purpose": other_purpose}
        )
        return render(request, self.template_name, {"form": form, "balance": balance})

    def post(self, request, pk):
        balance = get_object_or_404(StockBalance, pk=pk)
        require_location_access(request.user, balance.location)
        form = QuantityPurposeReclassifyForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "balance": balance})

        data = form.cleaned_data
        try:
            reclassify_quantity_purpose(
                user=request.user,
                product=balance.product,
                location=balance.location,
                from_purpose=data["from_purpose"],
                to_purpose=data["to_purpose"],
                quantity=data["quantity"],
                occurred_at=data["occurred_at"],
                reason=data["reason"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form, "balance": balance})

        messages.success(request, "Stock purpose updated.")
        return redirect(balance.get_absolute_url())
