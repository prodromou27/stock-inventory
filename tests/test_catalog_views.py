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


def _quick_add_management_form(num_forms):
    return {
        "form-TOTAL_FORMS": str(num_forms),
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
    }


def _quick_add_row(index, **fields):
    row = {
        f"form-{index}-brand_name": "",
        f"form-{index}-model": "",
        f"form-{index}-sku": "",
        f"form-{index}-product_type_name": "",
        f"form-{index}-tracking_method": "",
        f"form-{index}-supplier": "",
    }
    row.update({f"form-{index}-{key}": value for key, value in fields.items()})
    return row


@pytest.mark.django_db
class TestQuickAddProductsView:
    def test_anonymous_redirected_to_login(self, client):
        response = client.get(reverse("catalog:quick_add"))
        assert response.status_code == 302

    def test_read_only_user_cannot_access(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("catalog:quick_add"))
        assert response.status_code == 403

    def test_stock_manager_can_create_several_rows_at_once(self, client, stock_manager):
        client.force_login(stock_manager)
        data = _quick_add_management_form(2)
        data.update(
            _quick_add_row(
                0,
                brand_name="Cisco",
                model="RV340",
                product_type_name="Router",
                tracking_method="unit",
            )
        )
        data.update(
            _quick_add_row(
                1,
                brand_name="Cisco",
                model="RV345",
                product_type_name="Router",
                tracking_method="unit",
            )
        )
        response = client.post(reverse("catalog:quick_add"), data)
        assert response.status_code == 200
        assert [r["status"] for r in response.context["results"]] == ["created", "created"]
        assert Product.objects.filter(model="RV340").exists()
        assert Product.objects.filter(model="RV345").exists()

    def test_blank_rows_are_silently_skipped(self, client, stock_manager):
        client.force_login(stock_manager)
        data = _quick_add_management_form(3)
        data.update(
            _quick_add_row(0, brand_name="Cisco", model="RV340", product_type_name="Router")
        )
        data.update(_quick_add_row(1))
        data.update(_quick_add_row(2))
        response = client.post(reverse("catalog:quick_add"), data)
        assert response.status_code == 200
        assert len(response.context["results"]) == 1
        assert response.context["results"][0]["status"] == "created"

    def test_no_rows_entered_shows_error_without_creating_anything(self, client, stock_manager):
        client.force_login(stock_manager)
        data = _quick_add_management_form(3)
        data.update(_quick_add_row(0))
        data.update(_quick_add_row(1))
        data.update(_quick_add_row(2))
        response = client.post(reverse("catalog:quick_add"), data)
        assert response.status_code == 200
        assert response.context["no_rows_error"]
        assert Product.objects.count() == 0

    def test_partially_filled_row_reports_field_errors(self, client, stock_manager):
        client.force_login(stock_manager)
        data = _quick_add_management_form(1)
        data.update(_quick_add_row(0, brand_name="Cisco"))
        response = client.post(reverse("catalog:quick_add"), data)
        assert response.status_code == 200
        formset = response.context["formset"]
        assert not formset.is_valid()
        assert Product.objects.count() == 0

    def test_mixed_outcome_batch_reports_each_row(self, client, stock_manager, unit_product):
        client.force_login(stock_manager)
        data = _quick_add_management_form(2)
        data.update(
            _quick_add_row(
                0,
                brand_name=unit_product.brand.name,
                model=unit_product.model,
                product_type_name=unit_product.product_type.name,
                tracking_method="unit",
            )
        )
        data.update(
            _quick_add_row(
                1,
                brand_name="Cisco",
                model="RV340",
                product_type_name="Router",
                tracking_method="unit",
            )
        )
        response = client.post(reverse("catalog:quick_add"), data)
        assert response.status_code == 200
        statuses = [r["status"] for r in response.context["results"]]
        assert statuses == ["duplicate", "created"]


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
