"""Regression tests for a scope-leak found while building Phase 5 (documents):
assignment/delivery/reservation/disposition/correction transactions never set
a location on the InventoryTransaction *header* — only on their lines — so a
scope check that only looked at the header let any authenticated user view
any such transaction regardless of scope. Fixed in apps/inventory/access.py.
"""

from datetime import date

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.inventory.access import require_transaction_access
from apps.inventory.models import UnitAsset
from apps.inventory.services.assignments import assign_to_employee
from apps.inventory.services.disposition import mark_damaged
from apps.inventory.services.receipts import receive_stock
from apps.inventory.services.reservations import reserve_stock


@pytest.fixture
def other_room(administrator, other_location_tree):
    from apps.locations.models import Location
    from apps.locations.services import create_location

    other_floor = create_location(
        level=Location.Level.FLOOR,
        name="Access Floor",
        parent=other_location_tree["site"],
        user=administrator,
    )
    return create_location(
        level=Location.Level.STORAGE_ROOM,
        name="Access Room",
        parent=other_floor,
        user=administrator,
    )


@pytest.mark.django_db
class TestRequireTransactionAccess:
    def test_denies_assignment_transaction_outside_scope(
        self, administrator, stock_manager_with_room_access, unit_product, other_room
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-ACCESS-1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-ACCESS-1")
        assign_txn = assign_to_employee(
            user=administrator,
            employee_name="Rex",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        with pytest.raises(PermissionDenied):
            require_transaction_access(stock_manager_with_room_access, assign_txn)

    def test_allows_assignment_transaction_inside_scope(
        self, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-ACCESS-2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-ACCESS-2")
        assign_txn = assign_to_employee(
            user=stock_manager_with_room_access,
            employee_name="Sam",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        require_transaction_access(stock_manager_with_room_access, assign_txn)  # must not raise

    def test_denies_reservation_transaction_outside_scope(
        self, administrator, stock_manager_with_room_access, unit_product, other_room
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-ACCESS-3",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-ACCESS-3")
        reserve_txn = reserve_stock(
            user=administrator,
            occurred_at=date.today(),
            project_reference="PRJ-ACCESS",
            unit_asset_ids=[asset.pk],
        )

        with pytest.raises(PermissionDenied):
            require_transaction_access(stock_manager_with_room_access, reserve_txn)

    def test_denies_disposition_transaction_outside_scope(
        self, administrator, stock_manager_with_room_access, unit_product, other_room
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-ACCESS-4",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-ACCESS-4")
        damage_txn = mark_damaged(
            user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk], notes="dropped"
        )

        with pytest.raises(PermissionDenied):
            require_transaction_access(stock_manager_with_room_access, damage_txn)


@pytest.mark.django_db
class TestTransactionDetailViewScoping:
    def test_assignment_detail_denied_outside_scope(
        self, client, administrator, stock_manager_with_room_access, unit_product, other_room
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-ACCESS-5",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-ACCESS-5")
        assign_txn = assign_to_employee(
            user=administrator,
            employee_name="Tom",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:transaction_detail", kwargs={"pk": assign_txn.pk}))
        assert response.status_code == 403

    def test_read_only_user_without_any_grant_denied(
        self, client, administrator, read_only_user, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-ACCESS-6",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-ACCESS-6")
        assign_txn = assign_to_employee(
            user=administrator,
            employee_name="Uma",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        client.force_login(read_only_user)
        response = client.get(reverse("inventory:transaction_detail", kwargs={"pk": assign_txn.pk}))
        assert response.status_code == 403
