from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.catalog.models import ItemCategory
from apps.catalog.services import create_product, update_product
from apps.inventory.models import ProductLocationThreshold
from apps.inventory.services.receipts import receive_stock
from apps.reporting.queries import reorder_suggestions


def _configure_reorder(product, administrator, **kwargs):
    return update_product(
        product=product,
        user=administrator,
        brand_name=product.brand.name,
        model=product.model,
        product_type_name=product.product_type.name,
        category=product.category,
        low_stock_threshold=kwargs.pop("low_stock_threshold", 10),
        **kwargs,
    )


@pytest.mark.django_db
class TestReorderSuggestionsQuery:
    def test_configuration_required_when_no_target_is_set(
        self, administrator, quantity_product, location_tree
    ):
        _configure_reorder(quantity_product, administrator, low_stock_threshold=10)
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )
        rows = reorder_suggestions(administrator)
        assert len(rows) == 1
        assert rows[0]["configuration_required"] is True
        assert rows[0]["suggested_quantity"] is None

    def test_suggests_target_minus_available(self, administrator, quantity_product, location_tree):
        _configure_reorder(
            quantity_product,
            administrator,
            low_stock_threshold=10,
            target_stock_level=20,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )
        rows = reorder_suggestions(administrator)
        assert rows[0]["configuration_required"] is False
        assert rows[0]["suggested_quantity"] == 17  # 20 - 3

    def test_never_suggests_below_min_reorder_quantity(
        self, administrator, quantity_product, location_tree
    ):
        _configure_reorder(
            quantity_product,
            administrator,
            low_stock_threshold=10,
            target_stock_level=5,
            min_reorder_quantity=8,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )
        rows = reorder_suggestions(administrator)
        # target - available = 5 - 3 = 2, but min_reorder_quantity is 8
        assert rows[0]["suggested_quantity"] == 8

    def test_available_quantity_excludes_reserved(
        self, administrator, quantity_product, location_tree
    ):
        from apps.inventory.services.reservations import reserve_stock

        _configure_reorder(
            quantity_product, administrator, low_stock_threshold=10, target_stock_level=20
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        reserve_stock(
            user=administrator,
            occurred_at=date.today(),
            project_reference="PRJ-REORDER-TEST",
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 4}
            ],
        )
        rows = reorder_suggestions(administrator)
        assert rows[0]["available_quantity"] == 6  # 10 on hand - 4 reserved
        assert rows[0]["suggested_quantity"] == 14  # 20 - 6

    def test_above_threshold_is_not_suggested(self, administrator, quantity_product, location_tree):
        _configure_reorder(
            quantity_product, administrator, low_stock_threshold=5, target_stock_level=20
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=50,
        )
        assert reorder_suggestions(administrator) == []

    def test_location_override_wins_over_product_default(
        self, administrator, quantity_product, location_tree
    ):
        _configure_reorder(
            quantity_product, administrator, low_stock_threshold=10, target_stock_level=20
        )
        ProductLocationThreshold.objects.create(
            product=quantity_product,
            location=location_tree["room"],
            target_stock_level=50,
            created_by=administrator,
            updated_by=administrator,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )
        rows = reorder_suggestions(administrator)
        assert rows[0]["target_stock_level"] == 50
        assert rows[0]["suggested_quantity"] == 47

    def test_override_only_replaces_the_fields_it_sets(
        self, administrator, quantity_product, location_tree
    ):
        """A row can override just the preferred supplier while the target
        and min-reorder still fall back to the product's own global value.
        """
        _configure_reorder(
            quantity_product,
            administrator,
            low_stock_threshold=10,
            target_stock_level=20,
            preferred_supplier="Global Supplier",
        )
        ProductLocationThreshold.objects.create(
            product=quantity_product,
            location=location_tree["room"],
            preferred_supplier="Local Supplier",
            created_by=administrator,
            updated_by=administrator,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )
        rows = reorder_suggestions(administrator)
        assert rows[0]["preferred_supplier"] == "Local Supplier"
        assert rows[0]["target_stock_level"] == 20  # unaffected by the override

    def test_includes_last_receipt_info(self, administrator, quantity_product, location_tree):
        _configure_reorder(
            quantity_product, administrator, low_stock_threshold=10, target_stock_level=20
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
            invoice_number="INV-42",
        )
        rows = reorder_suggestions(administrator)
        assert rows[0]["last_receipt_date"] == date.today()
        assert rows[0]["last_receipt_quantity"] == 3
        assert rows[0]["last_invoice_number"] == "INV-42"

    def test_location_filter_scopes_results(
        self, administrator, quantity_product, location_tree, other_location_tree
    ):
        from apps.locations.models import Location
        from apps.locations.services import create_location

        _configure_reorder(
            quantity_product, administrator, low_stock_threshold=10, target_stock_level=20
        )
        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Reorder Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Reorder Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=other_room,
            occurred_at=date.today(),
            quantity=3,
        )
        rows = reorder_suggestions(administrator, location=location_tree["country"])
        assert len(rows) == 1
        assert rows[0]["location"] == location_tree["room"]


@pytest.mark.django_db
class TestProductReorderFieldsUnitTrackedGuard:
    def test_reorder_fields_are_cleared_for_unit_tracked_products(self, administrator):
        product = create_product(
            user=administrator,
            brand_name="Guard",
            model="Test",
            product_type_name="Gadget",
            category=ItemCategory.SERIALIZED_ASSET,
            target_stock_level=10,
            min_reorder_quantity=5,
            preferred_supplier="Should Be Cleared",
        )
        assert product.target_stock_level is None
        assert product.min_reorder_quantity is None
        assert product.preferred_supplier == ""

    def test_reorder_fields_persist_for_quantity_tracked_products(self, administrator):
        product = create_product(
            user=administrator,
            brand_name="Guard2",
            model="Test2",
            product_type_name="Gadget",
            category=ItemCategory.QUANTITY_STOCK,
            target_stock_level=10,
            min_reorder_quantity=5,
            preferred_supplier="Acme Supply",
        )
        assert product.target_stock_level == 10
        assert product.min_reorder_quantity == 5
        assert product.preferred_supplier == "Acme Supply"


@pytest.mark.django_db
class TestProductLocationThresholdModel:
    def test_unique_per_product_and_location(self, administrator, quantity_product, location_tree):
        ProductLocationThreshold.objects.create(
            product=quantity_product,
            location=location_tree["room"],
            target_stock_level=10,
            created_by=administrator,
            updated_by=administrator,
        )
        with pytest.raises(ValidationError):
            duplicate = ProductLocationThreshold(
                product=quantity_product,
                location=location_tree["room"],
                target_stock_level=20,
                created_by=administrator,
                updated_by=administrator,
            )
            duplicate.full_clean()


@pytest.mark.django_db
class TestReorderSuggestionsView:
    def test_requires_login(self, client):
        response = client.get(reverse("reporting:reorder_suggestions"))
        assert response.status_code == 302

    def test_shows_configuration_required(
        self, client, administrator, quantity_product, location_tree
    ):
        _configure_reorder(quantity_product, administrator, low_stock_threshold=10)
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )
        client.force_login(administrator)
        response = client.get(reverse("reporting:reorder_suggestions"))
        assert response.status_code == 200
        assert "Configuration required" in response.content.decode()

    def test_shows_suggested_quantity(self, client, administrator, quantity_product, location_tree):
        _configure_reorder(
            quantity_product, administrator, low_stock_threshold=10, target_stock_level=20
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )
        client.force_login(administrator)
        response = client.get(reverse("reporting:reorder_suggestions"))
        assert "Suggestion: 17" in response.content.decode()

    def test_csv_export(self, client, administrator, quantity_product, location_tree):
        _configure_reorder(
            quantity_product, administrator, low_stock_threshold=10, target_stock_level=20
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )
        client.force_login(administrator)
        response = client.get(reverse("reporting:reorder_suggestions"), {"format": "csv"})
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        body = response.content.decode()
        assert "17" in body

    def test_scoped_to_accessible_locations(
        self,
        client,
        stock_manager_with_room_access,
        administrator,
        quantity_product,
        location_tree,
        other_location_tree,
    ):
        from apps.locations.models import Location
        from apps.locations.services import create_location

        _configure_reorder(
            quantity_product, administrator, low_stock_threshold=10, target_stock_level=20
        )
        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Out of Scope Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Out of Scope Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=other_room,
            occurred_at=date.today(),
            quantity=3,
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("reporting:reorder_suggestions"))
        assert response.status_code == 200
        assert response.context["rows"] == []
