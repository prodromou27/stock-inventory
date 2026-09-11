from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.catalog.models import ItemCategory
from apps.catalog.services import create_product, update_product
from apps.inventory.models import ProductLocationThreshold
from apps.inventory.services.assignments import assign_to_employee
from apps.inventory.services.receipts import receive_stock
from apps.inventory.services.returns import return_stock
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
    def test_unit_assets_are_counted_per_country(
        self, administrator, unit_product, location_tree, other_location_tree
    ):
        _configure_reorder(
            unit_product,
            administrator,
            low_stock_threshold=2,
            target_stock_level=10,
        )
        for index in range(3):
            receive_stock(
                user=administrator,
                product=unit_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                vendor_serial=f"PRIMARY-{index}",
            )
        for index in range(2):
            receive_stock(
                user=administrator,
                product=unit_product,
                location=other_location_tree["room"],
                occurred_at=date.today(),
                vendor_serial=f"SECONDARY-{index}",
            )

        rows = reorder_suggestions(administrator)

        assert len(rows) == 1
        assert rows[0]["location"] == other_location_tree["country"]
        assert rows[0]["available_quantity"] == 2
        assert rows[0]["suggested_quantity"] == 8

        asset = unit_product.unit_assets.get(vendor_serial="PRIMARY-0")
        assignment = assign_to_employee(
            user=administrator,
            employee_name="Issued employee",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        rows = reorder_suggestions(administrator)
        assert {row["location"] for row in rows} == {
            location_tree["country"],
            other_location_tree["country"],
        }

        return_stock(
            user=administrator,
            original_transaction=assignment,
            location=location_tree["room"],
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        rows = reorder_suggestions(administrator)
        assert [row["location"] for row in rows] == [other_location_tree["country"]]

    def test_country_override_keeps_zero_available_unit_alert_visible(
        self, administrator, unit_product, location_tree
    ):
        ProductLocationThreshold.objects.create(
            product=unit_product,
            location=location_tree["country"],
            low_stock_threshold=2,
            target_stock_level=10,
            created_by=administrator,
            updated_by=administrator,
        )

        rows = reorder_suggestions(administrator)

        assert len(rows) == 1
        assert rows[0]["location"] == location_tree["country"]
        assert rows[0]["available_quantity"] == 0
        assert rows[0]["suggested_quantity"] == 10

    def test_query_count_does_not_grow_per_result(
        self, django_assert_max_num_queries, administrator, quantity_product, location_tree
    ):
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

        # Combined reporting has a fixed set of country/configuration queries
        # for unit assets in addition to the quantity-balance query.
        with django_assert_max_num_queries(12):
            assert len(reorder_suggestions(administrator)) == 1

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
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Reorder Room",
            parent=other_location_tree["country"],
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
class TestProductReorderFieldsUnitTracked:
    def test_reorder_fields_persist_for_unit_tracked_products(self, administrator):
        product = create_product(
            user=administrator,
            brand_name="Guard",
            model="Test",
            product_type_name="Gadget",
            category=ItemCategory.SERIALIZED_ASSET,
            low_stock_threshold=2,
            target_stock_level=10,
            min_reorder_quantity=5,
            preferred_supplier="Asset Supplier",
        )
        assert product.low_stock_threshold == 2
        assert product.target_stock_level == 10
        assert product.min_reorder_quantity == 5
        assert product.preferred_supplier == "Asset Supplier"

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
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Out of Scope Room",
            parent=other_location_tree["country"],
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


@pytest.mark.django_db
class TestReorderSettings:
    def test_administrator_can_reset_location_override_to_global(
        self, client, administrator, quantity_product, location_tree
    ):
        override = ProductLocationThreshold.objects.create(
            product=quantity_product,
            location=location_tree["room"],
            target_stock_level=40,
            created_by=administrator,
            updated_by=administrator,
        )
        client.force_login(administrator)
        response = client.post(reverse("reporting:reorder_settings_reset", args=[override.pk]))
        assert response.status_code == 302
        assert not ProductLocationThreshold.objects.filter(pk=override.pk).exists()
        assert AuditEvent.objects.filter(summary__contains="Reset reorder override").exists()

    def test_administrator_can_create_location_override(
        self, client, administrator, quantity_product, location_tree
    ):
        client.force_login(administrator)
        response = client.post(
            reverse("reporting:reorder_settings"),
            {
                "scope": "location",
                "product": quantity_product.pk,
                "location": location_tree["room"].pk,
                "low_stock_threshold": 4,
                "target_stock_level": 40,
                "min_reorder_quantity": 6,
                "preferred_supplier": "Local Supply",
            },
        )
        assert response.status_code == 302
        override = ProductLocationThreshold.objects.get(
            product=quantity_product, location=location_tree["room"]
        )
        assert override.target_stock_level == 40
        assert override.low_stock_threshold == 4
        assert override.min_reorder_quantity == 6
        assert override.preferred_supplier == "Local Supply"
        assert AuditEvent.objects.filter(object_id=str(override.pk)).exists()

    def test_administrator_can_update_counted_stock_settings(
        self, client, administrator, quantity_product
    ):
        client.force_login(administrator)
        response = client.post(
            reverse("reporting:reorder_settings"),
            {
                "product": quantity_product.pk,
                "low_stock_threshold": 8,
                "target_stock_level": 30,
                "min_reorder_quantity": 5,
                "preferred_supplier": "Supply Co",
            },
        )
        assert response.status_code == 302
        quantity_product.refresh_from_db()
        assert quantity_product.low_stock_threshold == 8
        assert quantity_product.target_stock_level == 30
        assert quantity_product.min_reorder_quantity == 5
        assert quantity_product.preferred_supplier == "Supply Co"

    def test_stock_manager_cannot_open_reorder_configuration(self, client, stock_manager):
        client.force_login(stock_manager)
        assert client.get(reverse("reporting:reorder_settings")).status_code == 403

    def test_unit_asset_override_requires_country(
        self, client, administrator, unit_product, location_tree
    ):
        client.force_login(administrator)
        response = client.post(
            reverse("reporting:reorder_settings"),
            {
                "scope": "location",
                "product": unit_product.pk,
                "location": location_tree["room"].pk,
                "low_stock_threshold": 2,
            },
        )
        assert response.status_code == 200
        assert "must be configured per country" in response.content.decode()

    def test_administrator_can_create_unit_asset_country_override(
        self, client, administrator, unit_product, location_tree
    ):
        client.force_login(administrator)
        response = client.post(
            reverse("reporting:reorder_settings"),
            {
                "scope": "location",
                "product": unit_product.pk,
                "location": location_tree["country"].pk,
                "low_stock_threshold": 2,
                "target_stock_level": 10,
            },
        )
        assert response.status_code == 302
        override = ProductLocationThreshold.objects.get(
            product=unit_product, location=location_tree["country"]
        )
        assert override.low_stock_threshold == 2
        assert override.target_stock_level == 10
