from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, RoleRequiredMixin

from .forms import ProductForm
from .models import Product
from .services import DuplicateProductError, create_product, update_product


class ProductListView(LoginRequiredMixin, ListView):
    model = Product
    template_name = "catalog/product_list.html"
    context_object_name = "products"
    paginate_by = 50

    def get_queryset(self):
        queryset = Product.objects.select_related("brand", "product_type")
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(brand__name__icontains=query)
                | Q(model__icontains=query)
                | Q(sku__icontains=query)
                | Q(product_type__name__icontains=query)
            )
        if self.request.GET.get("show_inactive") != "1":
            queryset = queryset.filter(is_active=True)
        return queryset.order_by("brand__name", "model")

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


class ProductCreateView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def get(self, request):
        form = ProductForm()
        return render(request, "catalog/product_form.html", {"form": form})

    def post(self, request):
        form = ProductForm(request.POST)
        if not form.is_valid():
            return render(request, "catalog/product_form.html", {"form": form})

        data = form.cleaned_data
        try:
            product = create_product(
                user=request.user,
                brand_name=data["brand_name"],
                model=data["model"],
                sku=data["sku"],
                product_type_name=data["product_type_name"],
                description=data["description"],
                tracking_method=data["tracking_method"],
                supplier=data["supplier"],
                default_notes=data["default_notes"],
                low_stock_threshold=data["low_stock_threshold"],
                duplicate_acknowledged=request.POST.get("duplicate_acknowledged") == "true",
            )
        except DuplicateProductError as exc:
            return render(
                request,
                "catalog/product_form.html",
                {"form": form, "duplicate_matches": exc.matches, "show_duplicate_warning": True},
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, "catalog/product_form.html", {"form": form})

        messages.success(request, f"Created product '{product}'.")
        return redirect(product.get_absolute_url())


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
                "tracking_method": product.tracking_method,
                "supplier": product.supplier,
                "default_notes": product.default_notes,
                "low_stock_threshold": product.low_stock_threshold,
                "is_active": product.is_active,
            }
        )
        return render(
            request,
            "catalog/product_form.html",
            {"form": form, "product": product, "locked": product.has_movements()},
        )

    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        form = ProductForm(request.POST)
        if not form.is_valid():
            return render(request, "catalog/product_form.html", {"form": form, "product": product})

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
                tracking_method=data["tracking_method"],
                supplier=data["supplier"],
                default_notes=data["default_notes"],
                low_stock_threshold=data["low_stock_threshold"],
                is_active=data["is_active"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, "catalog/product_form.html", {"form": form, "product": product})

        messages.success(request, f"Updated product '{product}'.")
        return redirect(product.get_absolute_url())
