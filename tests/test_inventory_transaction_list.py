from datetime import date

import pytest
from django.urls import reverse

from apps.inventory.models import UnitAsset
from apps.inventory.services.assignments import assign_to_employee
from apps.inventory.services.receipts import receive_stock


@pytest.fixture
def other_room(administrator, other_location_tree):
    from apps.locations.models import Location
    from apps.locations.services import create_location

    other_floor = create_location(
        level=Location.Level.FLOOR,
        name="TL Floor",
        parent=other_location_tree["site"],
        user=administrator,
    )
    return create_location(
        level=Location.Level.STORAGE_ROOM, name="TL Room", parent=other_floor, user=administrator
    )


@pytest.mark.django_db
class TestTransactionListView:
    def test_anonymous_redirected(self, client):
        response = client.get(reverse("inventory:transaction_list"))
        assert response.status_code == 302

    def test_scoped_to_accessible_locations_for_assignment_transaction(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        location_tree,
        other_room,
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-TL1",
        )
        asset1 = UnitAsset.objects.get(vendor_serial="SN-TL1")
        in_scope_txn = assign_to_employee(
            user=stock_manager_with_room_access,
            employee_name="A",
            occurred_at=date.today(),
            unit_asset_ids=[asset1.pk],
        )

        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-TL2",
        )
        asset2 = UnitAsset.objects.get(vendor_serial="SN-TL2")
        out_of_scope_txn = assign_to_employee(
            user=administrator,
            employee_name="B",
            occurred_at=date.today(),
            unit_asset_ids=[asset2.pk],
        )

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:transaction_list"))
        numbers = {t.transaction_number for t in response.context["transactions"]}
        assert in_scope_txn.transaction_number in numbers
        assert out_of_scope_txn.transaction_number not in numbers

    def test_administrator_sees_everything(
        self, client, administrator, unit_product, location_tree, other_room
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-TL3",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-TL3")
        txn = assign_to_employee(
            user=administrator,
            employee_name="C",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        client.force_login(administrator)
        response = client.get(reverse("inventory:transaction_list"))
        numbers = {t.transaction_number for t in response.context["transactions"]}
        assert txn.transaction_number in numbers

    def test_movement_type_filter(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-TL4",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-TL4")
        assign_txn = assign_to_employee(
            user=administrator,
            employee_name="D",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        client.force_login(administrator)
        response = client.get(reverse("inventory:transaction_list"), {"movement_type": "receipt"})
        numbers = {t.transaction_number for t in response.context["transactions"]}
        assert assign_txn.transaction_number not in numbers
