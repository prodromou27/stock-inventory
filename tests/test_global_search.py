from datetime import date

import pytest
from django.urls import reverse

from apps.inventory.services.receipts import receive_stock


@pytest.mark.django_db
class TestGlobalSearchView:
    """apps.core.views.GlobalSearchView — the top-bar search box in base.html."""

    def test_requires_login(self, client):
        response = client.get(reverse("core:search"), {"q": "anything"})
        assert response.status_code == 302

    def test_empty_query_returns_no_results(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("core:search"))
        assert response.status_code == 200
        assert response.context["products"] == []
        assert response.context["assets"] == []
        assert response.context["transactions"] == []

    def test_finds_product_by_model(self, client, administrator, unit_product):
        client.force_login(administrator)
        response = client.get(reverse("core:search"), {"q": unit_product.model})
        assert unit_product in response.context["products"]

    def test_finds_asset_by_serial(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-SEARCH-1",
        )
        client.force_login(administrator)
        response = client.get(reverse("core:search"), {"q": "SN-SEARCH-1"})
        serials = [asset.vendor_serial for asset in response.context["assets"]]
        assert "SN-SEARCH-1" in serials

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
        numbers = [t.transaction_number for t in response.context["transactions"]]
        assert txn.transaction_number in numbers

    def test_assets_scoped_to_accessible_locations(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        location_tree,
        other_location_tree,
    ):
        from apps.locations.models import Location
        from apps.locations.services import create_location

        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Search Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Search Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-OUT-OF-SCOPE",
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("core:search"), {"q": "SN-OUT-OF-SCOPE"})
        assert response.context["assets"] == []

    def test_finds_product_by_misspelled_model(self, client, administrator, unit_product):
        """Trigram-similarity ranking (apps.core.migrations.0002_enable_pg_trgm)
        should surface a close-but-not-exact match that isn't a literal substring
        match — unit_product.model is "FG-100F"; "FG-10F" (missing a digit) is
        not a substring of it, so only trigram similarity finds this."""
        assert unit_product.model == "FG-100F"
        client.force_login(administrator)
        response = client.get(reverse("core:search"), {"q": "FG-10F"})
        assert unit_product in response.context["products"]

    def test_products_are_not_location_scoped(
        self, client, stock_manager_with_room_access, unit_product
    ):
        """Products are catalog-global (apps.catalog has no dependency on
        apps.locations) — a Stock Manager can find any active product even
        though they only hold grants on specific locations."""
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("core:search"), {"q": unit_product.model})
        assert unit_product in response.context["products"]


@pytest.mark.django_db
class TestSearchSuggestView:
    """apps.core.views.SearchSuggestView — static/js/search.js's topbar
    live-preview dropdown. Same scoping/ranking as GlobalSearchView (built on
    the same _search_results() helper), just JSON instead of HTML."""

    def test_requires_login(self, client):
        response = client.get(reverse("core:search_suggest"), {"q": "anything"})
        assert response.status_code == 302

    def test_empty_query_returns_empty_lists(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("core:search_suggest"))
        assert response.status_code == 200
        data = response.json()
        assert data == {"query": "", "products": [], "assets": [], "transactions": []}

    def test_finds_product_with_label_and_url(self, client, administrator, unit_product):
        client.force_login(administrator)
        response = client.get(reverse("core:search_suggest"), {"q": unit_product.model})
        data = response.json()
        assert data["query"] == unit_product.model
        assert len(data["products"]) == 1
        assert data["products"][0]["label"] == str(unit_product)
        assert data["products"][0]["url"] == unit_product.get_absolute_url()

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

        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Suggest Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Suggest Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-SUGGEST-OUT-OF-SCOPE",
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("core:search_suggest"), {"q": "SN-SUGGEST-OUT-OF-SCOPE"})
        assert response.json()["assets"] == []

    def test_result_count_capped_below_full_page_limit(
        self, client, administrator, unit_product, location_tree
    ):
        from apps.core.views import SUGGEST_RESULT_LIMIT

        for i in range(SUGGEST_RESULT_LIMIT + 3):
            receive_stock(
                user=administrator,
                product=unit_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                vendor_serial=f"SN-SUGGEST-CAP-{i}",
            )
        client.force_login(administrator)
        response = client.get(reverse("core:search_suggest"), {"q": "SN-SUGGEST-CAP"})
        assert len(response.json()["assets"]) == SUGGEST_RESULT_LIMIT
