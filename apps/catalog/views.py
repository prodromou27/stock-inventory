from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, RoleRequiredMixin
from apps.core.csv_export import CSVExportMixin
from apps.core.recently_viewed import record_recently_viewed
from apps.core.sorting import SortableListMixin

from .forms import (
    ProductCustomFieldDefinitionForm,
    ProductForm,
    ProductGridFormSet,
    QuickAddProductFormSet,
    custom_field_key,
)
from .models import (
    CATEGORY_TRACKING_METHOD,
    Brand,
    Product,
    ProductCustomFieldDefinition,
    ProductType,
)
from .services import (
    DuplicateProductError,
    create_custom_field_definition,
    create_product,
    create_products_batch,
    set_custom_field_definition_active,
    update_product,
)

GRID_ROW_LIMIT = 50


def _catalog_choices():
    """Populates the <datalist> options behind ProductForm/QuickAddProductRowForm's
    brand_name/product_type_name inputs (apps.catalog.forms) — sorted, active
    names only. Typing a name not in this list still works exactly as before
    (get_or_create_brand/get_or_create_product_type in services.py); this is
    purely a convenience for picking an existing one, never a hard choice set.
    """
    return {
        "brand_choices": list(
            Brand.objects.filter(is_active=True).order_by("name").values_list("name", flat=True)
        ),
        "product_type_choices": list(
            ProductType.objects.filter(is_active=True)
            .order_by("name")
            .values_list("name", flat=True)
        ),
    }


def _filtered_products(request):
    """The search/show_inactive filtering shared by ProductListView and
    ProductGridView — factored out once the grid needed the identical
    "what is the operator currently looking at" set, minus sorting/
    pagination (each view applies those differently).
    """
    queryset = Product.objects.select_related("brand", "product_type")
    query = request.GET.get("q", "").strip()
    if query:
        queryset = queryset.filter(
            Q(brand__name__icontains=query)
            | Q(model__icontains=query)
            | Q(sku__icontains=query)
            | Q(product_type__name__icontains=query)
        )
    if request.GET.get("show_inactive") != "1":
        queryset = queryset.filter(is_active=True)
    return queryset


class ProductListView(LoginRequiredMixin, CSVExportMixin, SortableListMixin, ListView):
    model = Product
    template_name = "catalog/product_list.html"
    context_object_name = "products"
    paginate_by = 50
    csv_filename = "products.csv"
    csv_headers = [
        "Brand",
        "Model",
        "SKU",
        "Type",
        "Category",
        "Tracking method",
        "Supplier",
        "Status",
    ]

    sort_fields = {
        "brand": "brand__name",
        "model": "model",
        "sku": "sku",
        "type": "product_type__name",
        "status": "is_active",
    }
    default_ordering = ("brand__name", "model")

    def get_queryset(self):
        return self.apply_sort(_filtered_products(self.request))

    def csv_rows(self, queryset):
        for product in queryset:
            yield [
                product.brand.name,
                product.model,
                product.sku,
                product.product_type.name,
                product.get_category_display() if product.category else "",
                product.get_tracking_method_display(),
                product.supplier,
                "Active" if product.is_active else "Inactive",
            ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["query"] = self.request.GET.get("q", "")
        context["show_inactive"] = self.request.GET.get("show_inactive") == "1"
        return context


class ProductDetailView(LoginRequiredMixin, DetailView):
    model = Product
    template_name = "catalog/product_detail.html"
    context_object_name = "product"

    def get_queryset(self):
        return Product.objects.select_related("brand", "product_type")

    def get_template_names(self):
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return ["catalog/_product_detail_panel.html"]
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
        context["custom_field_rows"] = [
            (definition, context["product"].custom_field_values[str(definition.pk)])
            for definition in ProductCustomFieldDefinition.objects.filter(is_active=True)
            if str(definition.pk) in context["product"].custom_field_values
        ]
        return context


class ProductCreateView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def get(self, request):
        form = ProductForm()
        return render(request, "catalog/product_form.html", {"form": form, **_catalog_choices()})

    def post(self, request):
        form = ProductForm(request.POST)
        if not form.is_valid():
            return render(
                request, "catalog/product_form.html", {"form": form, **_catalog_choices()}
            )

        data = form.cleaned_data
        try:
            product = create_product(
                user=request.user,
                brand_name=data["brand_name"],
                model=data["model"],
                sku=data["sku"],
                product_type_name=data["product_type_name"],
                description=data["description"],
                category=data["category"],
                supplier=data["supplier"],
                default_notes=data["default_notes"],
                low_stock_threshold=data["low_stock_threshold"],
                duplicate_acknowledged=request.POST.get("duplicate_acknowledged") == "true",
                custom_field_values=form.get_custom_field_values(),
            )
        except DuplicateProductError as exc:
            return render(
                request,
                "catalog/product_form.html",
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
                request, "catalog/product_form.html", {"form": form, **_catalog_choices()}
            )

        messages.success(request, f"Created product '{product}'.")
        return redirect(product.get_absolute_url())


class QuickAddProductsView(LoginRequiredMixin, RoleRequiredMixin, View):
    """A formset of QuickAddProductFormSet's 10 rows — several products
    created from one submission instead of one page load each. Only rows
    the operator actually filled in are sent to create_products_batch();
    a blank row is silently skipped (Django's own empty_permitted formset
    behavior — see apps.catalog.forms.QuickAddProductRowForm), not an error.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "catalog/quick_add_products.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"formset": QuickAddProductFormSet(), **_catalog_choices()},
        )

    def post(self, request):
        formset = QuickAddProductFormSet(request.POST)
        if not formset.is_valid():
            return render(request, self.template_name, {"formset": formset, **_catalog_choices()})

        # A blank row's cleaned_data is a dict of empty strings, not an
        # empty dict (Form.full_clean() always sets self.cleaned_data = {}
        # before running field validation), so filter on the fields that
        # actually identify a product rather than dict truthiness — see
        # QuickAddProductRowForm.clean()'s docstring.
        rows = [row for row in formset.cleaned_data if row.get("brand_name")]
        if not rows:
            return render(
                request,
                self.template_name,
                {
                    "formset": formset,
                    "no_rows_error": "Enter at least one product.",
                    **_catalog_choices(),
                },
            )

        results = create_products_batch(user=request.user, rows=rows)

        created = sum(1 for r in results if r["status"] == "created")
        messages.success(request, f"Created {created} of {len(results)} product(s).")
        return render(
            request,
            self.template_name,
            {"formset": QuickAddProductFormSet(), "results": results, **_catalog_choices()},
        )


class ProductGridView(LoginRequiredMixin, RoleRequiredMixin, View):
    """A spreadsheet-style page for editing many *existing* products in one
    submission — apps.catalog.services.update_product() reused per changed
    row (see apps.catalog.forms.ProductGridRowForm's docstring). Reuses
    ProductListView's search/show_inactive filters so an operator can
    narrow down what they're editing, capped at GRID_ROW_LIMIT rows so a
    bulk-seeded database's full catalog never loads into one formset.
    """

    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    template_name = "catalog/product_grid.html"

    def _queryset(self, request):
        return _filtered_products(request).order_by("brand__name", "model")[:GRID_ROW_LIMIT]

    def _initial_rows(self, products):
        return [
            {
                "id": str(product.pk),
                "brand_name": product.brand.name,
                "model": product.model,
                "sku": product.sku,
                "product_type_name": product.product_type.name,
                "category": product.category,
                "supplier": product.supplier,
                "is_active": product.is_active,
            }
            for product in products
        ]

    def _render(self, request, formset, results=None):
        return render(
            request,
            self.template_name,
            {
                "formset": formset,
                "results": results,
                "row_limit": GRID_ROW_LIMIT,
                "query": request.GET.get("q", ""),
                "show_inactive": request.GET.get("show_inactive") == "1",
                **_catalog_choices(),
            },
        )

    def get(self, request):
        formset = ProductGridFormSet(initial=self._initial_rows(self._queryset(request)))
        return self._render(request, formset)

    def post(self, request):
        formset = ProductGridFormSet(request.POST)
        if not formset.is_valid():
            return self._render(request, formset)

        results = [self._apply_row(request.user, row) for row in formset.cleaned_data]

        updated = sum(1 for r in results if r["status"] == "updated")
        messages.success(request, f"Updated {updated} of {len(results)} product(s).")

        new_formset = ProductGridFormSet(initial=self._initial_rows(self._queryset(request)))
        return self._render(request, new_formset, results=results)

    def _apply_row(self, user, row):
        product = get_object_or_404(
            Product.objects.select_related("brand", "product_type"), pk=row["id"]
        )
        label = f"{row['brand_name']} {row['model']}"

        unchanged = (
            product.brand.name == row["brand_name"]
            and product.model == row["model"]
            and product.sku == row["sku"]
            and product.product_type.name == row["product_type_name"]
            and product.category == row["category"]
            and product.supplier == row["supplier"]
            and product.is_active == row["is_active"]
        )
        if unchanged:
            return {"label": label, "status": "unchanged"}

        new_tracking_method = CATEGORY_TRACKING_METHOD[row["category"]]
        if new_tracking_method != product.tracking_method and product.has_movements():
            return {
                "label": label,
                "status": "locked",
                "detail": (
                    "Category can't change to a different tracking method — this product "
                    "already has recorded movements."
                ),
            }

        try:
            update_product(
                product=product,
                user=user,
                brand_name=row["brand_name"],
                model=row["model"],
                sku=row["sku"],
                product_type_name=row["product_type_name"],
                description=product.description,
                category=row["category"],
                supplier=row["supplier"],
                default_notes=product.default_notes,
                low_stock_threshold=product.low_stock_threshold,
                is_active=row["is_active"],
                custom_field_values=product.custom_field_values,
            )
        except ValidationError as exc:
            return {
                "label": label,
                "status": "error",
                "detail": "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc),
            }
        return {"label": label, "status": "updated"}


class ProductUpdateView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def get(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        form = ProductForm(
            initial={
                "brand_name": product.brand.name,
                "model": product.model,
                "sku": product.sku,
                "product_type_name": product.product_type.name,
                "description": product.description,
                "category": product.category,
                "supplier": product.supplier,
                "default_notes": product.default_notes,
                "low_stock_threshold": product.low_stock_threshold,
                "is_active": product.is_active,
            }
        )
        # Custom-field initial values depend on which definitions are
        # currently active, which the form itself already resolved in
        # __init__ — reuse that instead of querying a second time.
        form.initial.update(
            {
                custom_field_key(d.pk): product.custom_field_values.get(str(d.pk))
                for d in form.custom_field_definitions
            }
        )
        return render(
            request,
            "catalog/product_form.html",
            {
                "form": form,
                "product": product,
                "locked": product.has_movements(),
                **_catalog_choices(),
            },
        )

    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        form = ProductForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                "catalog/product_form.html",
                {"form": form, "product": product, **_catalog_choices()},
            )

        data = form.cleaned_data
        try:
            update_product(
                product=product,
                user=request.user,
                brand_name=data["brand_name"],
                model=data["model"],
                sku=data["sku"],
                product_type_name=data["product_type_name"],
                description=data["description"],
                category=data["category"],
                supplier=data["supplier"],
                default_notes=data["default_notes"],
                low_stock_threshold=data["low_stock_threshold"],
                is_active=data["is_active"],
                custom_field_values=form.get_custom_field_values(),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(
                request,
                "catalog/product_form.html",
                {"form": form, "product": product, **_catalog_choices()},
            )

        messages.success(request, f"Updated product '{product}'.")
        return redirect(product.get_absolute_url())


# --- Product custom fields ("from Settings", user request) ---------------


class ProductCustomFieldDefinitionListView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "catalog/custom_field_list.html"

    def get(self, request):
        definitions = ProductCustomFieldDefinition.objects.all()
        return render(request, self.template_name, {"definitions": definitions})


class ProductCustomFieldDefinitionCreateView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "catalog/custom_field_form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": ProductCustomFieldDefinitionForm()})

    def post(self, request):
        form = ProductCustomFieldDefinitionForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        try:
            create_custom_field_definition(user=request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form})

        messages.success(request, "Custom field created.")
        return redirect("catalog:custom_field_list")


class ProductCustomFieldDefinitionToggleActiveView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, pk):
        definition = get_object_or_404(ProductCustomFieldDefinition, pk=pk)
        new_is_active = not definition.is_active
        set_custom_field_definition_active(
            definition=definition, user=request.user, is_active=new_is_active
        )
        messages.success(
            request, f"{'Activated' if new_is_active else 'Deactivated'} '{definition.name}'."
        )
        return redirect("catalog:custom_field_list")
