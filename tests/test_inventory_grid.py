import json
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
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

    def test_manipulated_location_param_outside_scope_returns_empty_not_leaked(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        other_location_tree,
    ):
        """A tampered ?location= naming a real Location the operator has no
        access to must yield zero rows, not the other country's data —
        apps.inventory.filters._filter_by_location() only ever narrows the
        already-scoped queryset (never a fresh, unscoped one), so this
        should already hold by construction; this test proves it instead of
        just assuming it.
        """
        from apps.locations.models import Location
        from apps.locations.services import create_location

        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Param Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Param Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-GRID-PARAM-LEAK",
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(
            reverse("inventory:asset_grid_data"), {"location": str(other_room.pk)}
        )
        assert response.status_code == 200
        assert response.json()["data"] == []

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
        "field",
        [
            "status",
            "current_location",
            "vendor_serial",
            "arrival_date",
            "stock_purpose",
            "current_custody_transaction",
        ],
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

    def test_denied_for_off_storage_asset_outside_scope(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        location_tree,
        other_location_tree,
    ):
        """An off-storage asset (current_location=NULL after assignment)
        must still be scoped by its last known location — regression test
        for require_location_access(None) silently allowing an inline edit
        on any off-storage asset regardless of the country it came from.
        """
        from apps.accounts.services import grant_location_access
        from apps.inventory.services.assignments import assign_to_employee

        User = get_user_model()
        other_manager = User.objects.create_user(
            username="grid-other-country-manager", password="a-strong-test-password-123"
        )
        other_manager.groups.add(Group.objects.get(name="StockManager"))
        grant_location_access(
            user=other_manager, location=other_location_tree["country"], granted_by=administrator
        )

        asset = self._asset(
            stock_manager_with_room_access, unit_product, location_tree, "SN-EDIT-OFFSTORAGE"
        )
        assign_to_employee(
            user=stock_manager_with_room_access,
            employee_name="Someone",
            occurred_at=date.today(),
            unit_asset_ids=[str(asset.pk)],
        )
        asset.refresh_from_db()
        assert asset.current_location_id is None

        client.force_login(other_manager)
        response = client.post(
            reverse("inventory:asset_grid_field_update", kwargs={"pk": asset.pk}),
            data=json.dumps({"field": "notes", "value": "hacked"}),
            content_type="application/json",
        )
        assert response.status_code == 403
        asset.refresh_from_db()
        assert asset.notes != "hacked"

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

    def test_products_grid_key_is_valid(self, client, administrator):
        """Was a live bug before grid_key became an open, registry-validated
        field (apps.inventory.services.grid_views.VALID_GRID_KEYS) —
        templates/catalog/product_list.html already posts grid_key='products'
        even though it was never one of the old 2-value choices=.
        """
        client.force_login(administrator)
        response = client.post(
            self._list_create_url(grid_key="products"),
            data=json.dumps({"name": "My product view", "state": {}}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["name"] == "My product view"

    def test_unknown_grid_key_is_still_rejected(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            self._list_create_url(grid_key="not-a-real-grid"),
            data=json.dumps({"name": "Nope", "state": {}}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "Unknown grid" in response.json()["error"]

    def test_create_as_default_unsets_the_previous_default(self, client, administrator):
        client.force_login(administrator)
        first = client.post(
            self._list_create_url(),
            data=json.dumps({"name": "First", "state": {}, "is_default": True}),
            content_type="application/json",
        ).json()
        second = client.post(
            self._list_create_url(),
            data=json.dumps({"name": "Second", "state": {}, "is_default": True}),
            content_type="application/json",
        ).json()
        assert second["is_default"] is True
        assert not SavedGridView.objects.get(pk=first["id"]).is_default
        assert SavedGridView.objects.get(pk=second["id"]).is_default


@pytest.mark.django_db
class TestSavedGridViewUpdateAPI:
    """apps.inventory.views.SavedGridViewUpdateView / apps.inventory.services.
    grid_views.update_saved_grid_view() — real rename/update-in-place,
    replacing the old delete-and-recreate-only workflow.
    """

    def _update_url(self, pk):
        return reverse("inventory:saved_grid_view_update", kwargs={"pk": pk})

    def test_owner_can_rename(self, client, administrator):
        view = SavedGridView.objects.create(
            name="Old name",
            grid_key="assets",
            state={"density": "compact"},
            created_by=administrator,
            updated_by=administrator,
        )
        original_pk = view.pk
        client.force_login(administrator)
        response = client.post(
            self._update_url(view.pk),
            data=json.dumps({"name": "New name"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["id"] == str(original_pk)  # same row, not delete+recreate
        assert response.json()["name"] == "New name"
        view.refresh_from_db()
        assert view.pk == original_pk
        assert view.name == "New name"
        assert view.state == {"density": "compact"}  # untouched when omitted

    def test_owner_can_set_and_unset_default(self, client, administrator):
        view = SavedGridView.objects.create(
            name="Mine",
            grid_key="assets",
            state={},
            created_by=administrator,
            updated_by=administrator,
        )
        client.force_login(administrator)
        response = client.post(
            self._update_url(view.pk),
            data=json.dumps({"is_default": True}),
            content_type="application/json",
        )
        assert response.json()["is_default"] is True
        view.refresh_from_db()
        assert view.is_default is True

        client.post(
            self._update_url(view.pk),
            data=json.dumps({"is_default": False}),
            content_type="application/json",
        )
        view.refresh_from_db()
        assert view.is_default is False

    def test_setting_default_unsets_the_previous_one(self, client, administrator):
        first = SavedGridView.objects.create(
            name="First",
            grid_key="assets",
            state={},
            is_default=True,
            created_by=administrator,
            updated_by=administrator,
        )
        second = SavedGridView.objects.create(
            name="Second",
            grid_key="assets",
            state={},
            created_by=administrator,
            updated_by=administrator,
        )
        client.force_login(administrator)
        client.post(
            self._update_url(second.pk),
            data=json.dumps({"is_default": True}),
            content_type="application/json",
        )
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.is_default is False
        assert second.is_default is True

    def test_non_owner_non_admin_cannot_update(
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
        response = client.post(
            self._update_url(view.pk),
            data=json.dumps({"name": "Hijacked"}),
            content_type="application/json",
        )
        assert response.status_code == 403
        view.refresh_from_db()
        assert view.name == "Not yours"

    def test_blank_name_is_rejected(self, client, administrator):
        view = SavedGridView.objects.create(
            name="Keep me",
            grid_key="assets",
            state={},
            created_by=administrator,
            updated_by=administrator,
        )
        client.force_login(administrator)
        response = client.post(
            self._update_url(view.pk),
            data=json.dumps({"name": "   "}),
            content_type="application/json",
        )
        assert response.status_code == 400
        view.refresh_from_db()
        assert view.name == "Keep me"


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
        from apps.catalog.models import ItemCategory
        from apps.catalog.services import create_product

        other_product = create_product(
            user=administrator,
            brand_name="Zeta",
            model="Z-9",
            product_type_name="Widget",
            category=ItemCategory.QUANTITY_STOCK,
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


@pytest.mark.django_db
class TestProductGridDataView:
    """apps.inventory.views.ProductGridDataView — the Products grid's JSON
    data source (templates/catalog/product_list.html)."""

    def test_requires_login(self, client):
        response = client.get(reverse("inventory:product_grid_data"))
        assert response.status_code == 302

    def test_lists_products_globally_not_location_scoped(
        self, client, stock_manager_with_room_access, unit_product
    ):
        # Products are catalog-global — a Stock Manager sees every product
        # even though their location access is narrow.
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:product_grid_data"))
        assert response.status_code == 200
        models = [row["model"] for row in response.json()["data"]]
        assert unit_product.model in models

    def test_inactive_excluded_unless_show_inactive(self, client, administrator, unit_product):
        unit_product.is_active = False
        unit_product.save(update_fields=["is_active"])
        client.force_login(administrator)
        response = client.get(reverse("inventory:product_grid_data"))
        models = [row["model"] for row in response.json()["data"]]
        assert unit_product.model not in models

        response = client.get(reverse("inventory:product_grid_data"), {"show_inactive": "1"})
        models = [row["model"] for row in response.json()["data"]]
        assert unit_product.model in models

    def test_search_narrows_results(self, client, administrator, unit_product, quantity_product):
        client.force_login(administrator)
        response = client.get(reverse("inventory:product_grid_data"), {"q": unit_product.model})
        models = [row["model"] for row in response.json()["data"]]
        assert unit_product.model in models
        assert quantity_product.model not in models

    def test_low_stock_badge_flags_available_at_or_below_threshold(
        self, client, administrator, quantity_product, location_tree
    ):
        # quantity_product's fixture already sets low_stock_threshold=5.
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=5,
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:product_grid_data"))
        row = next(r for r in response.json()["data"] if r["model"] == quantity_product.model)
        assert row["available"] == 5
        assert row["is_low_stock"] is True

    def test_available_above_threshold_is_not_low_stock(
        self, client, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=50,
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:product_grid_data"))
        row = next(r for r in response.json()["data"] if r["model"] == quantity_product.model)
        assert row["available"] == 50
        assert row["is_low_stock"] is False

    def test_no_balance_row_reports_available_as_none(
        self, client, administrator, quantity_product
    ):
        client.force_login(administrator)
        response = client.get(reverse("inventory:product_grid_data"))
        row = next(r for r in response.json()["data"] if r["model"] == quantity_product.model)
        assert row["available"] is None
        assert row["is_low_stock"] is False

    def test_low_stock_available_scoped_to_accessible_locations(
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
            name="Product Grid Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Product Grid Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=other_room,
            occurred_at=date.today(),
            quantity=50,
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:product_grid_data"))
        row = next(r for r in response.json()["data"] if r["model"] == quantity_product.model)
        # The Stock Manager can't see other_room's balance at all, so the
        # product looks like it has no recorded stock in their scope — never
        # a (false) global total that includes locations they can't access.
        assert row["available"] is None


@pytest.mark.django_db
class TestAssetPickerDataView:
    """apps.inventory.views.AssetPickerDataView — the mass-select grid
    embedded in templates/inventory/_asset_picker.html, used by Transfer/
    Reserve/Assign/Deliver/AssessReturn/MarkDamaged/MarkLost/Dispose/
    RepairDamaged."""

    def test_requires_login(self, client):
        response = client.get(reverse("inventory:asset_picker_data"))
        assert response.status_code == 302

    def test_missing_statuses_returns_no_rows(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_picker_data"))
        assert response.status_code == 200
        assert response.json()["data"] == []

    def test_filters_by_requested_statuses(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PICKER-INSTOCK",
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_picker_data"), {"statuses": "damaged"})
        serials = [row["serial"] for row in response.json()["data"]]
        assert "SN-PICKER-INSTOCK" not in serials

        response = client.get(
            reverse("inventory:asset_picker_data"), {"statuses": "in_stock,reserved"}
        )
        serials = [row["serial"] for row in response.json()["data"]]
        assert "SN-PICKER-INSTOCK" in serials

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
            name="Picker Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Picker Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-PICKER-OUT-OF-SCOPE",
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:asset_picker_data"), {"statuses": "in_stock"})
        serials = [row["serial"] for row in response.json()["data"]]
        assert "SN-PICKER-OUT-OF-SCOPE" not in serials

    def test_search_narrows_results(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PICKER-SEARCH-1",
        )
        client.force_login(administrator)
        response = client.get(
            reverse("inventory:asset_picker_data"),
            {"statuses": "in_stock", "serial": "SN-PICKER-SEARCH-1"},
        )
        serials = [row["serial"] for row in response.json()["data"]]
        assert serials == ["SN-PICKER-SEARCH-1"]

    def test_preselected_rows_sort_first_and_are_flagged(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PICKER-PRESELECT-A",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PICKER-PRESELECT-B",
        )
        target = UnitAsset.objects.get(vendor_serial="SN-PICKER-PRESELECT-B")
        client.force_login(administrator)
        response = client.get(
            reverse("inventory:asset_picker_data"),
            {"statuses": "in_stock", "preselected": str(target.pk)},
        )
        data = response.json()["data"]
        assert data[0]["id"] == str(target.pk)
        assert data[0]["preselected"] is True
        assert all(row["preselected"] is False for row in data[1:])


@pytest.mark.django_db
class TestAssetGridStockPurposeAndAssignedTo:
    """New serialized fields on UnitAssetGridDataView — stock_purpose (new
    classification) and assigned_to (the current custody pointer's display
    block). See docs/architecture/09-delivery-backlog.md's dated entry.
    """

    def test_stock_purpose_serialized_and_filterable(
        self, client, administrator, unit_product, location_tree
    ):
        from apps.inventory.models import StockPurpose

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-GRID-PURPOSE-INT",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-GRID-PURPOSE-CUST",
            stock_purpose=StockPurpose.CUSTOMER,
            final_customer="Acme Co",
        )
        client.force_login(administrator)

        response = client.get(reverse("inventory:asset_grid_data"), {"stock_purpose": "customer"})
        serials = [row["serial"] for row in response.json()["data"]]
        assert serials == ["SN-GRID-PURPOSE-CUST"]

        response = client.get(reverse("inventory:asset_grid_data"))
        row = next(r for r in response.json()["data"] if r["serial"] == "SN-GRID-PURPOSE-CUST")
        assert row["stock_purpose"] == "customer"
        assert row["stock_purpose_display"] == "Customer"

    def test_assigned_to_block_present_when_assigned_and_absent_when_in_stock(
        self, client, administrator, unit_product, location_tree
    ):
        from apps.inventory.services.assignments import assign_to_employee

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-GRID-ASSIGNED",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-GRID-IN-STOCK",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-GRID-ASSIGNED")
        assign_to_employee(
            user=administrator,
            employee_name="Jane Doe",
            recipient_reference="EMP-1",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_grid_data"), {"status": ""})
        rows_by_serial = {r["serial"]: r for r in response.json()["data"]}

        assigned_row = rows_by_serial["SN-GRID-ASSIGNED"]
        assert assigned_row["assigned_to"]["type"] == "employee"
        assert assigned_row["assigned_to"]["name"] == "Jane Doe"
        assert assigned_row["assigned_to"]["reference"] == "EMP-1"

        in_stock_row = rows_by_serial["SN-GRID-IN-STOCK"]
        assert in_stock_row["assigned_to"] is None

    def test_assigned_to_block_has_the_same_shape_for_a_customer_delivery(
        self, client, administrator, unit_product, location_tree
    ):
        """apps.inventory.views._assigned_to_block() must show a customer
        delivery's custodian info with the exact same field set an employee
        assignment gets (only "type"/"type_display"/"name" differ) — the
        plan's phase 7 parity check.
        """
        from apps.inventory.services.assignments import deliver_to_customer

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-GRID-DELIVERED",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-GRID-DELIVERED")
        deliver_to_customer(
            user=administrator,
            final_customer="Acme Corp",
            recipient_reference="CUST-1",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_grid_data"), {"status": ""})
        row = next(r for r in response.json()["data"] if r["serial"] == "SN-GRID-DELIVERED")

        assert row["assigned_to"]["type"] == "customer"
        assert row["assigned_to"]["type_display"] == "Customer"
        assert row["assigned_to"]["name"] == "Acme Corp"
        assert row["assigned_to"]["reference"] == "CUST-1"
        assert set(row["assigned_to"].keys()) == {
            "type",
            "type_display",
            "name",
            "reference",
            "project_reference",
            "transaction_id",
            "transaction_number",
            "transaction_url",
            "date",
            "expected_return_date",
            "notes",
        }
