import pytest
from django.urls import reverse

from apps.catalog.models import Product


@pytest.mark.django_db
class TestProductListView:
    def test_anonymous_redirected_to_login(self, client):
        response = client.get(reverse("catalog:product_list"))
        assert response.status_code == 302

    def test_read_only_user_can_view_list(self, client, read_only_user, unit_product):
        client.force_login(read_only_user)
        response = client.get(reverse("catalog:product_list"))
        assert response.status_code == 200
        names = [p.model for p in response.context["products"]]
        assert unit_product.model in names

    def test_search_filters_by_model(self, client, read_only_user, unit_product, quantity_product):
        client.force_login(read_only_user)
        response = client.get(reverse("catalog:product_list"), {"q": unit_product.model})
        names = [p.model for p in response.context["products"]]
        assert unit_product.model in names
        assert quantity_product.model not in names

    def test_sort_by_brand_descending(self, client, read_only_user, unit_product, quantity_product):
        # unit_product is Fortinet, quantity_product is HP (tests/conftest.py) —
        # HP sorts after Fortinet ascending, so descending puts it first.
        client.force_login(read_only_user)
        response = client.get(reverse("catalog:product_list"), {"sort": "brand", "dir": "desc"})
        brands = [p.brand.name for p in response.context["products"]]
        assert brands.index("HP") < brands.index("Fortinet")

    def test_unknown_sort_key_falls_back_to_default(self, client, read_only_user, unit_product):
        client.force_login(read_only_user)
        response = client.get(reverse("catalog:product_list"), {"sort": "not-a-field"})
        assert response.status_code == 200


@pytest.mark.django_db
class TestProductDetailView:
    def test_any_authenticated_role_can_view_detail(self, client, read_only_user, unit_product):
        client.force_login(read_only_user)
        response = client.get(reverse("catalog:product_detail", kwargs={"pk": unit_product.pk}))
        assert response.status_code == 200


@pytest.mark.django_db
class TestProductCreateView:
    def test_read_only_user_cannot_create(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.post(
            reverse("catalog:product_create"),
            {
                "brand_name": "Fortinet",
                "model": "FG-100F",
                "product_type_name": "Firewall",
                "tracking_method": "unit",
            },
        )
        assert response.status_code == 403
        assert not Product.objects.filter(model="FG-100F").exists()

    def test_stock_manager_can_create(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.post(
            reverse("catalog:product_create"),
            {
                "brand_name": "Fortinet",
                "model": "FG-100F",
                "product_type_name": "Firewall",
                "tracking_method": "unit",
            },
        )
        assert response.status_code == 302
        assert Product.objects.filter(model="FG-100F").exists()

    def test_duplicate_shows_warning_instead_of_creating(self, client, stock_manager, unit_product):
        client.force_login(stock_manager)
        response = client.post(
            reverse("catalog:product_create"),
            {
                "brand_name": unit_product.brand.name,
                "model": unit_product.model,
                "product_type_name": unit_product.product_type.name,
                "tracking_method": "unit",
            },
        )
        assert response.status_code == 200
        assert response.context["show_duplicate_warning"] is True
        assert Product.objects.filter(model=unit_product.model).count() == 1

    def test_duplicate_acknowledged_creates_second_product(
        self, client, stock_manager, unit_product
    ):
        client.force_login(stock_manager)
        response = client.post(
            reverse("catalog:product_create"),
            {
                "brand_name": unit_product.brand.name,
                "model": unit_product.model,
                "product_type_name": unit_product.product_type.name,
                "tracking_method": "unit",
                "duplicate_acknowledged": "true",
            },
        )
        assert response.status_code == 302
        assert Product.objects.filter(model=unit_product.model).count() == 2


@pytest.mark.django_db
class TestProductUpdateView:
    def test_read_only_user_cannot_edit(self, client, read_only_user, unit_product):
        client.force_login(read_only_user)
        response = client.post(
            reverse("catalog:product_update", kwargs={"pk": unit_product.pk}),
            {
                "brand_name": unit_product.brand.name,
                "model": unit_product.model,
                "product_type_name": unit_product.product_type.name,
                "tracking_method": unit_product.tracking_method,
                "description": "hacked",
            },
        )
        assert response.status_code == 403
        unit_product.refresh_from_db()
        assert unit_product.description != "hacked"

    def test_stock_manager_can_edit(self, client, stock_manager, unit_product):
        client.force_login(stock_manager)
        response = client.post(
            reverse("catalog:product_update", kwargs={"pk": unit_product.pk}),
            {
                "brand_name": unit_product.brand.name,
                "model": unit_product.model,
                "product_type_name": unit_product.product_type.name,
                "tracking_method": unit_product.tracking_method,
                "description": "updated",
            },
        )
        assert response.status_code == 302
        unit_product.refresh_from_db()
        assert unit_product.description == "updated"
