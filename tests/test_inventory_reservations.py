from datetime import date

import pytest
from django.core.exceptions import ValidationError

from apps.inventory.models import (
    ReservationStatus,
    StockBalance,
    StockReservation,
    UnitAsset,
    UnitStatus,
)
from apps.inventory.services.receipts import receive_stock
from apps.inventory.services.reservations import release_reservation, reserve_stock


@pytest.mark.django_db
class TestReserveStock:
    def test_unit_reservation_sets_status_reserved(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-RES-1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-RES-1")

        reserve_stock(
            user=administrator,
            occurred_at=date.today(),
            project_reference="PRJ-1",
            unit_asset_ids=[asset.pk],
        )
        asset.refresh_from_db()
        assert asset.status == UnitStatus.RESERVED
        assert asset.current_location == location_tree["room"]  # stays physically in place

    def test_quantity_reservation_creates_reservation_row_and_reserves_balance(
        self, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=20,
        )

        reserve_stock(
            user=administrator,
            occurred_at=date.today(),
            project_reference="PRJ-2",
            final_customer="Acme",
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 5}
            ],
        )

        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert balance.reserved_quantity == 5
        assert balance.available_quantity == 15
        reservation = StockReservation.objects.get(
            product=quantity_product, location=location_tree["room"]
        )
        assert reservation.status == ReservationStatus.ACTIVE
        assert reservation.project_reference == "PRJ-2"

    def test_project_reference_required(self, administrator, quantity_product, location_tree):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        with pytest.raises(ValidationError):
            reserve_stock(
                user=administrator,
                occurred_at=date.today(),
                project_reference="",
                quantity_lines=[
                    {"product": quantity_product, "location": location_tree["room"], "quantity": 1}
                ],
            )

    def test_cannot_reserve_more_than_available(
        self, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=5,
        )
        with pytest.raises(ValidationError):
            reserve_stock(
                user=administrator,
                occurred_at=date.today(),
                project_reference="PRJ-3",
                quantity_lines=[
                    {"product": quantity_product, "location": location_tree["room"], "quantity": 6}
                ],
            )
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert balance.reserved_quantity == 0

    def test_cannot_reserve_already_reserved_asset(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-RES-2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-RES-2")
        reserve_stock(
            user=administrator,
            occurred_at=date.today(),
            project_reference="PRJ-4",
            unit_asset_ids=[asset.pk],
        )

        with pytest.raises(ValidationError):
            reserve_stock(
                user=administrator,
                occurred_at=date.today(),
                project_reference="PRJ-5",
                unit_asset_ids=[asset.pk],
            )


@pytest.mark.django_db
class TestReleaseReservation:
    def test_releases_unit_reservation(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-REL-1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-REL-1")
        reserve_stock(
            user=administrator,
            occurred_at=date.today(),
            project_reference="PRJ-6",
            unit_asset_ids=[asset.pk],
        )

        release_reservation(user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk])
        asset.refresh_from_db()
        assert asset.status == UnitStatus.IN_STOCK

    def test_releases_quantity_reservation(self, administrator, quantity_product, location_tree):
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
            project_reference="PRJ-7",
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 4}
            ],
        )
        reservation = StockReservation.objects.get(product=quantity_product)

        release_reservation(
            user=administrator, occurred_at=date.today(), reservations=[reservation]
        )

        reservation.refresh_from_db()
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert reservation.status == ReservationStatus.RELEASED
        assert balance.reserved_quantity == 0
        assert balance.available_quantity == 10

    def test_cannot_release_already_released_reservation(
        self, administrator, quantity_product, location_tree
    ):
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
            project_reference="PRJ-8",
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 2}
            ],
        )
        reservation = StockReservation.objects.get(product=quantity_product)
        release_reservation(
            user=administrator, occurred_at=date.today(), reservations=[reservation]
        )

        with pytest.raises(ValidationError):
            release_reservation(
                user=administrator, occurred_at=date.today(), reservations=[reservation]
            )
