from datetime import date

import pytest
from django.core.exceptions import ValidationError

from apps.inventory.models import StockBalance, UnitAsset, UnitStatus
from apps.inventory.services.disposition import (
    dispose,
    mark_damaged,
    mark_lost,
    return_repaired_to_stock,
)
from apps.inventory.services.receipts import receive_stock


@pytest.mark.django_db
class TestMarkDamaged:
    def test_stock_manager_can_return_repaired_asset_to_stock(
        self, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-REPAIRED",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-REPAIRED")
        mark_damaged(
            user=stock_manager_with_room_access,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            notes="failed power supply",
        )
        txn = return_repaired_to_stock(
            user=stock_manager_with_room_access,
            location=location_tree["room"],
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            notes="power supply replaced",
        )
        asset.refresh_from_db()
        assert asset.status == UnitStatus.IN_STOCK
        assert asset.current_location == location_tree["room"]
        assert txn.lines.get().from_status == UnitStatus.DAMAGED

    def test_marks_damaged_without_moving_location(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DMG1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DMG1")

        mark_damaged(
            user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk], notes="dropped"
        )
        asset.refresh_from_db()
        assert asset.status == UnitStatus.DAMAGED
        assert asset.current_location == location_tree["room"]
        assert asset.last_removal_date is None

    def test_requires_notes(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DMG2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DMG2")
        with pytest.raises(ValidationError):
            mark_damaged(
                user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk], notes=""
            )

    def test_quantity_damage_decrements_balance(
        self, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        mark_damaged(
            user=administrator,
            occurred_at=date.today(),
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 3}
            ],
            notes="water damage",
        )
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert balance.on_hand_quantity == 7


@pytest.mark.django_db
class TestMarkLost:
    def test_marks_lost_and_clears_location(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-LOST1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-LOST1")

        mark_lost(
            user=administrator,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            notes="audit could not find",
        )
        asset.refresh_from_db()
        assert asset.status == UnitStatus.LOST
        assert asset.current_location is None
        assert asset.last_removal_date == date.today()

    def test_lost_asset_cannot_be_recovered_without_admin_correction(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-LOST2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-LOST2")
        mark_lost(
            user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk], notes="missing"
        )

        with pytest.raises(ValidationError):
            mark_lost(
                user=administrator,
                occurred_at=date.today(),
                unit_asset_ids=[asset.pk],
                notes="again",
            )


@pytest.mark.django_db
class TestDispose:
    def test_disposes_asset(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DISP1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DISP1")

        dispose(
            user=administrator,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            notes="end of life",
        )
        asset.refresh_from_db()
        assert asset.status == UnitStatus.DISPOSED
        assert asset.current_location is None

    def test_disposed_hdd_remains_searchable(self, administrator, location_tree):
        from apps.catalog.models import TrackingMethod
        from apps.catalog.services import create_product

        hdd = create_product(
            user=administrator,
            brand_name="Seagate",
            model="ST1000",
            product_type_name="HDD",
            tracking_method=TrackingMethod.UNIT,
        )
        receive_stock(
            user=administrator,
            product=hdd,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="HDD-001",
        )
        asset = UnitAsset.objects.get(vendor_serial="HDD-001")
        dispose(
            user=administrator,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            notes="sanitized and disposed",
        )

        # disposed assets are never deleted, and remain queryable
        assert UnitAsset.objects.filter(
            vendor_serial="HDD-001", status=UnitStatus.DISPOSED
        ).exists()

    def test_disposed_asset_is_terminal(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DISP2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DISP2")
        dispose(
            user=administrator,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            notes="scrapped",
        )

        with pytest.raises(ValidationError):
            mark_damaged(
                user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk], notes="x"
            )
