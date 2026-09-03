import json
from datetime import date

import pytest
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.inventory.models import SavedGridView, UnitAsset
from apps.inventory.services.receipts import receive_stock


@pytest.mark.django_db
class TestUnitAssetGridDataView:
    """apps.inventory.views.UnitAssetGridDataView — the JSON data source
    behind templates/inventory/asset_list.html's Tabulator grid."""

    def test_requires_login(self, client):
        response = client.get(reverse("inventory:asset_grid_data"))
        assert response.status_code == 302

    def test_returns_scoped_paginated_rows(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-GRID-1",
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_grid_data"), {"page": 1, "size": 10})
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] >= 1
        serials = [row["serial"] for row in data["data"]]
        assert "SN-GRID-1" in serials
        row = next(r for r in data["data"] if r["serial"] == "SN-GRID-1")
        assert row["country"] == location_tree["country"].name
        assert row["storage_room"] == location_tree["room"].name

    def test_scoped_to_accessible_locations(
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
            name="Grid Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Grid Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-GRID-OUT-OF-SCOPE",
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:asset_grid_data"))
        serials = [row["serial"] for row in response.json()["data"]]
        assert "SN-GRID-OUT-OF-SCOPE" not in serials

    def test_multi_column_sort(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-GRID-SORT-A",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-GRID-SORT-B",
        )
        client.force_login(administrator)
        response = client.get(
            reverse("inventory:asset_grid_data"), {"sort": ["serial:desc"], "size": 200}
        )
        serials = [row["serial"] for row in response.json()["data"]]
        assert serials.index("SN-GRID-SORT-B") < serials.index("SN-GRID-SORT-A")

    def test_filters_reuse_filter_unit_assets(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-GRID-FILTER",
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_grid_data"), {"status": "damaged"})
        serials = [row["serial"] for row in response.json()["data"]]
        assert "SN-GRID-FILTER" not in serials

    def test_quick_actions_empty_for_read_only_user(
        self, client, read_only_user, administrator, unit_product, location_tree
    ):
        from apps.accounts.services import grant_location_access

        grant_location_access(
            user=read_only_user, location=location_tree["room"], granted_by=administrator
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-GRID-RO",
        )
        client.force_login(read_only_user)
        response = client.get(reverse("inventory:asset_grid_data"))
        row = next(r for r in response.json()["data"] if r["serial"] == "SN-GRID-RO")
        assert row["quick_actions"] == []

    def test_quick_actions_present_for_administrator(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-GRID-ADMIN",
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_grid_data"))
        row = next(r for r in response.json()["data"] if r["serial"] == "SN-GRID-ADMIN")
        assert any(a["label"] == "Transfer" for a in row["quick_actions"])


@pytest.mark.django_db
class TestAssetGridFieldUpdateView:
    """apps.inventory.views.AssetGridFieldUpdateView — the grid's inline-edit
    save endpoint. Only ever touches the 5 plain descriptive fields."""

    def _asset(self, administrator, unit_product, location_tree, serial):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial=serial,
        )
        return UnitAsset.objects.get(vendor_serial=serial)

    def test_requires_login(self, client, administrator, unit_product, location_tree):
        asset = self._asset(administrator, unit_product, location_tree, "SN-EDIT-AUTH")
        response = client.post(
            reverse("inventory:asset_grid_field_update", kwargs={"pk": asset.pk}),
            data=json.dumps({"field": "notes", "value": "hi"}),
            content_type="application/json",
        )
        assert response.status_code == 302

    def test_read_only_user_forbidden(
        self, client, read_only_user, administrator, unit_product, location_tree
    ):
        asset = self._asset(administrator, unit_product, location_tree, "SN-EDIT-RO")
        client.force_login(read_only_user)
        response = client.post(
            reverse("inventory:asset_grid_field_update", kwargs={"pk": asset.pk}),
            data=json.dumps({"field": "notes", "value": "hi"}),
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_updates_allowed_field_and_records_audit_event(
        self, client, administrator, unit_product, location_tree
    ):
        asset = self._asset(administrator, unit_product, location_tree, "SN-EDIT-OK")
        client.force_login(administrator)
        response = client.post(
            reverse("inventory:asset_grid_field_update", kwargs={"pk": asset.pk}),
            data=json.dumps({"field": "project_reference", "value": "PROJ-123"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        asset.refresh_from_db()
        assert asset.project_reference == "PROJ-123"
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.RECORD_UPDATED,
            object_type="UnitAsset",
            object_id=str(asset.pk),
        ).exists()

    @pytest.mark.parametrize(
        "field", ["status", "current_location", "vendor_serial", "arrival_date"]
    )
    def test_rejects_disallowed_fields(
        self, client, administrator, unit_product, location_tree, field
    ):
        asset = self._asset(administrator, unit_product, location_tree, f"SN-EDIT-DENY-{field}")
        client.force_login(administrator)
        response = client.post(
            reverse("inventory:asset_grid_field_update", kwargs={"pk": asset.pk}),
            data=json.dumps({"field": field, "value": "hacked"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        asset.refresh_from_db()
        assert getattr(asset, field) != "hacked"

    def test_scoped_to_accessible_locations(
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
            name="Edit Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Edit Room",
            parent=other_floor,
            user=administrator,
        )
        asset = self._asset(administrator, unit_product, {"room": other_room}, "SN-EDIT-SCOPE")
        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:asset_grid_field_update", kwargs={"pk": asset.pk}),
            data=json.dumps({"field": "notes", "value": "hi"}),
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_invalid_json_body_returns_400(
        self, client, administrator, unit_product, location_tree
    ):
        asset = self._asset(administrator, unit_product, location_tree, "SN-EDIT-BADJSON")
        client.force_login(administrator)
        response = client.post(
            reverse("inventory:asset_grid_field_update", kwargs={"pk": asset.pk}),
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestSavedGridViewAPI:
    """apps.inventory.views.SavedGridViewListCreateView / SavedGridViewDeleteView
    — the grid's "Views" dropdown, mirroring apps.reporting's SavedReport
    ownership/sharing model."""

    def _list_create_url(self, grid_key="assets"):
        return reverse("inventory:saved_grid_view_list_create", kwargs={"grid_key": grid_key})

    def test_requires_login(self, client):
        response = client.get(self._list_create_url())
        assert response.status_code == 302

    def test_create_and_list_own_view(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            self._list_create_url(),
            data=json.dumps({"name": "My view", "state": {"density": "compact"}}),
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "My view"
        assert body["is_shared"] is False

        listing = client.get(self._list_create_url()).json()
        assert any(v["name"] == "My view" and v["is_mine"] for v in listing["views"])

    def test_non_admin_cannot_share(self, client, stock_manager_with_room_access):
        client.force_login(stock_manager_with_room_access)
        response = client.post(
            self._list_create_url(),
            data=json.dumps({"name": "Shared attempt", "state": {}, "is_shared": True}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["is_shared"] is False

    def test_administrator_can_share(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            self._list_create_url(),
            data=json.dumps({"name": "Shared view", "state": {}, "is_shared": True}),
            content_type="application/json",
        )
        assert response.json()["is_shared"] is True

    def test_user_sees_own_and_shared_but_not_others_private(
        self, client, administrator, stock_manager_with_room_access
    ):
        SavedGridView.objects.create(
            name="Admin private",
            grid_key="assets",
            state={},
            is_shared=False,
            created_by=administrator,
            updated_by=administrator,
        )
        SavedGridView.objects.create(
            name="Admin shared",
            grid_key="assets",
            state={},
            is_shared=True,
            created_by=administrator,
            updated_by=administrator,
        )
        client.force_login(stock_manager_with_room_access)
        names = {v["name"] for v in client.get(self._list_create_url()).json()["views"]}
        assert "Admin shared" in names
        assert "Admin private" not in names

    def test_owner_can_delete(self, client, administrator):
        view = SavedGridView.objects.create(
            name="Deletable",
            grid_key="assets",
            state={},
            created_by=administrator,
            updated_by=administrator,
        )
        client.force_login(administrator)
        response = client.post(reverse("inventory:saved_grid_view_delete", kwargs={"pk": view.pk}))
        assert response.status_code == 200
        assert not SavedGridView.objects.filter(pk=view.pk).exists()

    def test_non_owner_non_admin_cannot_delete(
        self, client, administrator, stock_manager_with_room_access
    ):
        view = SavedGridView.objects.create(
            name="Not yours",
            grid_key="assets",
            state={},
            is_shared=True,
            created_by=administrator,
            updated_by=administrator,
        )
        client.force_login(stock_manager_with_room_access)
        response = client.post(reverse("inventory:saved_grid_view_delete", kwargs={"pk": view.pk}))
        assert response.status_code == 403
        assert SavedGridView.objects.filter(pk=view.pk).exists()

    def test_state_too_large_is_rejected(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            self._list_create_url(),
            data=json.dumps({"name": "Huge", "state": {"blob": "x" * 30_000}}),
            content_type="application/json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestStockBalanceGridDataView:
    """apps.inventory.views.StockBalanceGridDataView — the Stock Balances
    grid's JSON data source (templates/inventory/balance_list.html)."""

    def test_requires_login(self, client):
        response = client.get(reverse("inventory:balance_grid_data"))
        assert response.status_code == 302

    def test_returns_scoped_rows_with_available_quantity(
        self, client, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:balance_grid_data"))
        assert response.status_code == 200
        data = response.json()
        row = next(r for r in data["data"] if r["brand"] == quantity_product.brand.name)
        assert row["on_hand"] == 10
        assert row["available"] == 10
        assert row["country"] == location_tree["country"].name

    def test_scoped_to_accessible_locations(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        quantity_product,
        other_location_tree,
    ):
        from apps.locations.models import Location
        from apps.locations.services import create_location

        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Balance Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Balance Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=other_room,
            occurred_at=date.today(),
            quantity=5,
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:balance_grid_data"))
        locations = [row["location"] for row in response.json()["data"]]
        assert "Balance Room" not in locations

    def test_sort_by_available_quantity(
        self, client, administrator, quantity_product, location_tree
    ):
        from apps.catalog.models import TrackingMethod
        from apps.catalog.services import create_product

        other_product = create_product(
            user=administrator,
            brand_name="Zeta",
            model="Z-9",
            product_type_name="Widget",
            tracking_method=TrackingMethod.QUANTITY,
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
            product=other_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=30,
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:balance_grid_data"), {"sort": ["available:desc"]})
        available_values = [row["available"] for row in response.json()["data"]]
        assert available_values == sorted(available_values, reverse=True)
        assert available_values[0] == 30
