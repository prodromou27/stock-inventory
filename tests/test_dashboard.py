from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.inventory.models import UnitAsset
from apps.inventory.services.disposition import mark_damaged, mark_lost
from apps.inventory.services.receipts import receive_stock
from apps.inventory.services.reservations import reserve_stock
from apps.reporting.queries import dashboard_summary


@pytest.mark.django_db
class TestDashboardSummary:
    """apps.core.views.HomeView's stat cards."""

    def test_counts_in_stock_units(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DASH-1",
        )
        stats = dashboard_summary(administrator)
        assert stats["assets_in_stock"] == 1

    def test_sums_quantity_on_hand_across_balances(
        self, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=7,
        )
        stats = dashboard_summary(administrator)
        assert stats["quantity_on_hand"] == 7

    def test_zero_quantity_on_hand_when_no_balances_exist(self, administrator):
        stats = dashboard_summary(administrator)
        assert stats["quantity_on_hand"] == 0

    def test_counts_active_reservations(self, administrator, quantity_product, location_tree):
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
            project_reference="PROJ-DASH",
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 3}
            ],
        )
        stats = dashboard_summary(administrator)
        assert stats["active_reservations"] == 1

    def test_counts_damaged_and_lost_separately(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DASH-DAMAGED",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DASH-LOST",
        )
        damaged_asset = UnitAsset.objects.get(vendor_serial="SN-DASH-DAMAGED")
        lost_asset = UnitAsset.objects.get(vendor_serial="SN-DASH-LOST")
        mark_damaged(
            user=administrator,
            unit_asset_ids=[damaged_asset.pk],
            occurred_at=date.today(),
            notes="Dropped in transit",
        )
        mark_lost(
            user=administrator,
            unit_asset_ids=[lost_asset.pk],
            occurred_at=date.today(),
            notes="Not found during audit",
        )
        stats = dashboard_summary(administrator)
        assert stats["damaged_count"] == 1
        assert stats["lost_count"] == 1

    def test_recent_transactions_excludes_older_than_seven_days(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today() - timedelta(days=10),
            vendor_serial="SN-DASH-OLD",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DASH-NEW",
        )
        stats = dashboard_summary(administrator)
        assert stats["recent_transactions"] == 1

    def test_scoped_to_accessible_locations(
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
            name="Dash Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Dash Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DASH-IN-SCOPE",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-DASH-OUT-OF-SCOPE",
        )

        stats = dashboard_summary(stock_manager_with_room_access)
        assert stats["assets_in_stock"] == 1


@pytest.mark.django_db
class TestHomeViewStats:
    def test_dashboard_shows_stat_cards(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DASH-VIEW",
        )
        client.force_login(administrator)
        response = client.get(reverse("core:home"))
        assert response.status_code == 200
        assert response.context["stats"]["assets_in_stock"] == 1
        assert "Units in stock" in response.content.decode()
