from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.inventory.models import StockBalance, UnitStatus
from apps.inventory.services.receipts import receive_stock
from apps.inventory.services.transfers import bulk_transfer


@pytest.fixture
def rack(administrator, location_tree):
    from apps.locations.models import Location
    from apps.locations.services import create_location

    return create_location(
        level=Location.Level.RACK_CABINET,
        name="Rack A",
        parent=location_tree["room"],
        user=administrator,
    )


@pytest.mark.django_db
class TestBulkTransfer:
    def test_transfers_unit_asset_keeping_status(
        self, administrator, unit_product, location_tree, rack
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-T1",
        )
        from apps.inventory.models import UnitAsset

        asset = UnitAsset.objects.get(vendor_serial="SN-T1")

        txn = bulk_transfer(
            user=administrator,
            destination_location=rack,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        asset.refresh_from_db()
        assert asset.current_location == rack
        assert asset.status == UnitStatus.IN_STOCK
        assert txn.lines.count() == 1

    def test_transfers_quantity_between_balances(
        self, administrator, quantity_product, location_tree, rack
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=20,
        )

        bulk_transfer(
            user=administrator,
            destination_location=rack,
            occurred_at=date.today(),
            quantity_lines=[
                {
                    "product": quantity_product,
                    "source_location": location_tree["room"],
                    "quantity": 8,
                }
            ],
        )

        source = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        dest = StockBalance.objects.get(product=quantity_product, location=rack)
        assert source.on_hand_quantity == 12
        assert dest.on_hand_quantity == 8

    def test_mixed_unit_and_quantity_lines_in_one_transaction(
        self, administrator, unit_product, quantity_product, location_tree, rack
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-MIX",
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        from apps.inventory.models import UnitAsset

        asset = UnitAsset.objects.get(vendor_serial="SN-MIX")

        txn = bulk_transfer(
            user=administrator,
            destination_location=rack,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            quantity_lines=[
                {
                    "product": quantity_product,
                    "source_location": location_tree["room"],
                    "quantity": 4,
                }
            ],
        )
        assert txn.lines.count() == 2

    def test_cannot_transfer_insufficient_quantity(
        self, administrator, quantity_product, location_tree, rack
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=5,
        )
        with pytest.raises(ValidationError):
            bulk_transfer(
                user=administrator,
                destination_location=rack,
                occurred_at=date.today(),
                quantity_lines=[
                    {
                        "product": quantity_product,
                        "source_location": location_tree["room"],
                        "quantity": 6,
                    }
                ],
            )
        source = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert source.on_hand_quantity == 5  # untouched by the failed attempt

    def test_cannot_transfer_assigned_asset(self, administrator, unit_product, location_tree, rack):
        from apps.inventory.models import UnitAsset
        from apps.inventory.services.assignments import assign_to_employee

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-ASSIGNED",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-ASSIGNED")
        assign_to_employee(
            user=administrator,
            employee_name="Bob",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        with pytest.raises(ValidationError):
            bulk_transfer(
                user=administrator,
                destination_location=rack,
                occurred_at=date.today(),
                unit_asset_ids=[asset.pk],
            )

    def test_scope_enforced_on_source_and_destination(
        self, stock_manager, unit_product, location_tree, rack
    ):
        with pytest.raises(PermissionDenied):
            bulk_transfer(
                user=stock_manager,
                destination_location=rack,
                occurred_at=date.today(),
                quantity_lines=[],
                unit_asset_ids=[],
            )

    def test_read_only_user_cannot_transfer(self, read_only_user, rack):
        with pytest.raises(Exception):
            bulk_transfer(user=read_only_user, destination_location=rack, occurred_at=date.today())

    def test_requires_at_least_one_line(self, administrator, rack):
        with pytest.raises(ValidationError):
            bulk_transfer(user=administrator, destination_location=rack, occurred_at=date.today())
