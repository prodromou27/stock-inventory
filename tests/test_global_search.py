from datetime import date

import pytest
from django.urls import reverse

from apps.inventory.models import UnitAsset
from apps.inventory.services.assignments import assign_to_employee, deliver_to_customer
from apps.inventory.services.receipts import receive_stock
from apps.inventory.services.returns import return_stock


def receive_asset(user, product, room, serial):
    receive_stock(
        user=user, product=product, location=room, occurred_at=date.today(), vendor_serial=serial
    )
    return UnitAsset.objects.get(vendor_serial=serial)


@pytest.mark.django_db
class TestGlobalSearchView:
    def test_requires_login(self, client):
        assert client.get(reverse("core:search"), {"q": "anything"}).status_code == 302

    def test_empty_query_returns_no_results(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("core:search"))
        assert response.status_code == 200
        assert response.context["assets"] == []
        assert response.context["transactions"] == []

    def test_finds_asset_by_product_model(self, client, administrator, unit_product, location_tree):
        asset = receive_asset(administrator, unit_product, location_tree["room"], "SN-MODEL-SEARCH")
        client.force_login(administrator)
        response = client.get(reverse("core:search"), {"q": unit_product.model})
        assert asset in response.context["assets"]
        assert "Products" not in response.content.decode()

    def test_finds_asset_by_serial(self, client, administrator, unit_product, location_tree):
        asset = receive_asset(administrator, unit_product, location_tree["room"], "SN-SEARCH-1")
        client.force_login(administrator)
        response = client.get(reverse("core:search"), {"q": "SN-SEARCH-1"})
        assert asset in response.context["assets"]

    def test_finds_transaction_by_number(self, client, administrator, unit_product, location_tree):
        txn = receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-SEARCH-2",
        )
        client.force_login(administrator)
        response = client.get(reverse("core:search"), {"q": txn.transaction_number})
        assert txn in response.context["transactions"]

    def test_assets_scoped_to_accessible_locations(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        other_location_tree,
    ):
        from apps.locations.models import Location
        from apps.locations.services import create_location

        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Search Room",
            parent=other_location_tree["country"],
            user=administrator,
        )
        receive_asset(administrator, unit_product, other_room, "SN-OUT-OF-SCOPE")
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("core:search"), {"q": "SN-OUT-OF-SCOPE"})
        assert response.context["assets"] == []

    def test_finds_asset_by_misspelled_model(
        self, client, administrator, unit_product, location_tree
    ):
        asset = receive_asset(administrator, unit_product, location_tree["room"], "SN-FUZZY-MODEL")
        client.force_login(administrator)
        response = client.get(reverse("core:search"), {"q": "FG-10F"})
        assert asset in response.context["assets"]

    def test_finds_assigned_asset_by_employee_name(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        asset = receive_asset(
            stock_manager_with_room_access,
            unit_product,
            location_tree["room"],
            "SN-EMPLOYEE-SEARCH",
        )
        assign_to_employee(
            user=stock_manager_with_room_access,
            employee_name="Alexandra Unique Recipient",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("core:search"), {"q": "Alexandra Unique"})
        assert asset in response.context["assets"]
        assert "Assigned to" in response.content.decode()
        assert "Alexandra Unique Recipient" in response.content.decode()

    def test_finds_delivered_asset_by_customer_name(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        asset = receive_asset(
            stock_manager_with_room_access,
            unit_product,
            location_tree["room"],
            "SN-CUSTOMER-SEARCH",
        )
        deliver_to_customer(
            user=stock_manager_with_room_access,
            final_customer="Northwind Unique Customer",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        client.force_login(stock_manager_with_room_access)

        response = client.get(reverse("core:search"), {"q": "Northwind Unique"})

        assert asset in response.context["assets"]
        assert "Northwind Unique Customer" in response.content.decode()

    def test_recipient_search_includes_asset_after_it_is_returned(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        asset = receive_asset(
            stock_manager_with_room_access,
            unit_product,
            location_tree["room"],
            "SN-HISTORICAL-RECIPIENT",
        )
        assignment = assign_to_employee(
            user=stock_manager_with_room_access,
            employee_name="Historical Employee Unique",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        return_stock(
            user=stock_manager_with_room_access,
            original_transaction=assignment,
            location=location_tree["room"],
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        client.force_login(stock_manager_with_room_access)

        response = client.get(reverse("core:search"), {"q": "Historical Employee"})

        asset.refresh_from_db()
        assert asset in response.context["assets"]
        assert asset.status == "returned"


@pytest.mark.django_db
class TestSearchSuggestView:
    def test_requires_login(self, client):
        assert client.get(reverse("core:search_suggest"), {"q": "anything"}).status_code == 302

    def test_empty_query_returns_empty_lists(self, client, administrator):
        client.force_login(administrator)
        assert client.get(reverse("core:search_suggest")).json() == {
            "query": "",
            "assets": [],
            "transactions": [],
        }

    def test_finds_asset_from_product_metadata(
        self, client, administrator, unit_product, location_tree
    ):
        asset = receive_asset(
            administrator, unit_product, location_tree["room"], "SN-SUGGEST-MODEL"
        )
        client.force_login(administrator)
        data = client.get(reverse("core:search_suggest"), {"q": unit_product.model}).json()
        assert "products" not in data
        assert data["assets"][0] == {"label": str(asset), "url": asset.get_absolute_url()}

    def test_employee_search_suggests_assigned_asset(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        asset = receive_asset(
            stock_manager_with_room_access,
            unit_product,
            location_tree["room"],
            "SN-EMPLOYEE-SUGGEST",
        )
        assign_to_employee(
            user=stock_manager_with_room_access,
            employee_name="Jamie Searchable",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        client.force_login(stock_manager_with_room_access)

        data = client.get(reverse("core:search_suggest"), {"q": "Jamie"}).json()

        assert data["assets"] == [
            {"label": f"{asset} — Jamie Searchable", "url": asset.get_absolute_url()}
        ]

    def test_assets_scoped_to_accessible_locations(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        other_location_tree,
    ):
        from apps.locations.models import Location
        from apps.locations.services import create_location

        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Suggest Room",
            parent=other_location_tree["country"],
            user=administrator,
        )
        receive_asset(administrator, unit_product, other_room, "SN-SUGGEST-OUT-OF-SCOPE")
        client.force_login(stock_manager_with_room_access)
        data = client.get(reverse("core:search_suggest"), {"q": "SN-SUGGEST-OUT-OF-SCOPE"}).json()
        assert data["assets"] == []

    def test_result_count_capped_below_full_page_limit(
        self, client, administrator, unit_product, location_tree
    ):
        from apps.core.views import SUGGEST_RESULT_LIMIT

        for i in range(SUGGEST_RESULT_LIMIT + 3):
            receive_asset(administrator, unit_product, location_tree["room"], f"SN-SUGGEST-CAP-{i}")
        client.force_login(administrator)
        data = client.get(reverse("core:search_suggest"), {"q": "SN-SUGGEST-CAP"}).json()
        assert len(data["assets"]) == SUGGEST_RESULT_LIMIT
