from datetime import date

import pytest
from django.core.exceptions import ValidationError

from apps.audit.models import AuditEvent
from apps.inventory.models import StockBalance, UnitAsset, UnitStatus
from apps.inventory.services.corrections import (
    correct_balance,
    correct_unit_status,
    reverse_transaction,
)
from apps.inventory.services.disposition import dispose, mark_lost
from apps.inventory.services.receipts import receive_stock
from apps.inventory.services.reservations import reserve_stock
from apps.inventory.services.transfers import bulk_transfer


@pytest.mark.django_db
class TestCorrectUnitStatus:
    def test_administrator_can_force_any_status(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-C1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-C1")
        mark_lost(
            user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk], notes="missing"
        )

        correct_unit_status(
            user=administrator,
            unit_asset=asset,
            to_status=UnitStatus.IN_STOCK,
            occurred_at=date.today(),
            reason="found during audit",
            to_location=location_tree["room"],
        )
        asset.refresh_from_db()
        assert asset.status == UnitStatus.IN_STOCK
        assert asset.current_location == location_tree["room"]

    def test_reason_required(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-C2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-C2")
        with pytest.raises(ValidationError):
            correct_unit_status(
                user=administrator,
                unit_asset=asset,
                to_status=UnitStatus.DAMAGED,
                occurred_at=date.today(),
                reason="",
            )

    def test_stock_manager_cannot_correct(
        self, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-C3",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-C3")
        with pytest.raises(Exception):
            correct_unit_status(
                user=stock_manager_with_room_access,
                unit_asset=asset,
                to_status=UnitStatus.DAMAGED,
                occurred_at=date.today(),
                reason="x",
            )

    def test_correction_is_audited_with_old_and_new_values(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-C4",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-C4")

        correct_unit_status(
            user=administrator,
            unit_asset=asset,
            to_status=UnitStatus.DAMAGED,
            occurred_at=date.today(),
            reason="found damaged during audit",
        )
        event = AuditEvent.objects.filter(event_type=AuditEvent.EventType.ADMIN_CORRECTION).latest(
            "occurred_at"
        )
        assert event.old_values["status"] == UnitStatus.IN_STOCK
        assert event.new_values["status"] == UnitStatus.DAMAGED


@pytest.mark.django_db
class TestCorrectBalance:
    def test_administrator_can_set_balance_directly(
        self, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        correct_balance(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            new_on_hand_quantity=50,
            occurred_at=date.today(),
            reason="physical count discrepancy",
        )
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert balance.on_hand_quantity == 50

    def test_cannot_correct_to_negative(self, administrator, quantity_product, location_tree):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        with pytest.raises(ValidationError):
            correct_balance(
                user=administrator,
                product=quantity_product,
                location=location_tree["room"],
                new_on_hand_quantity=-5,
                occurred_at=date.today(),
                reason="test",
            )

    def test_reason_required(self, administrator, quantity_product, location_tree):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        with pytest.raises(ValidationError):
            correct_balance(
                user=administrator,
                product=quantity_product,
                location=location_tree["room"],
                new_on_hand_quantity=20,
                occurred_at=date.today(),
                reason="",
            )


@pytest.mark.django_db
class TestReverseTransaction:
    def test_reverses_disposal(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-REV1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-REV1")
        dispose_txn = dispose(
            user=administrator,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            notes="scrapped by mistake",
        )

        reverse_transaction(
            user=administrator,
            original_transaction=dispose_txn,
            occurred_at=date.today(),
            reason="mistake",
        )
        asset.refresh_from_db()
        assert asset.status == UnitStatus.IN_STOCK
        assert asset.current_location == location_tree["room"]

    def test_reverses_quantity_receipt(self, administrator, quantity_product, location_tree):
        receipt_txn = receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=15,
        )

        reverse_transaction(
            user=administrator,
            original_transaction=receipt_txn,
            occurred_at=date.today(),
            reason="entered twice",
        )
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert balance.on_hand_quantity == 0

    def test_cannot_reverse_twice(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-REV2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-REV2")
        dispose_txn = dispose(
            user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk], notes="x"
        )
        reverse_transaction(
            user=administrator,
            original_transaction=dispose_txn,
            occurred_at=date.today(),
            reason="x",
        )

        with pytest.raises(ValidationError):
            reverse_transaction(
                user=administrator,
                original_transaction=dispose_txn,
                occurred_at=date.today(),
                reason="x",
            )

    def test_cannot_reverse_if_asset_moved_on(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-REV3",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-REV3")
        dispose_txn = dispose(
            user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk], notes="x"
        )
        # an intervening admin correction moves the asset again
        correct_unit_status(
            user=administrator,
            unit_asset=asset,
            to_status=UnitStatus.IN_STOCK,
            occurred_at=date.today(),
            reason="recovered",
            to_location=location_tree["room"],
        )

        with pytest.raises(ValidationError):
            reverse_transaction(
                user=administrator,
                original_transaction=dispose_txn,
                occurred_at=date.today(),
                reason="too late",
            )

    def test_original_transaction_is_never_mutated(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-REV4",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-REV4")
        dispose_txn = dispose(
            user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk], notes="x"
        )
        original_line_count = dispose_txn.lines.count()

        reverse_transaction(
            user=administrator,
            original_transaction=dispose_txn,
            occurred_at=date.today(),
            reason="x",
        )

        assert dispose_txn.lines.count() == original_line_count

    def test_read_only_user_cannot_reverse(
        self, read_only_user, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-REV5",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-REV5")
        dispose_txn = dispose(
            user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk], notes="x"
        )

        with pytest.raises(Exception):
            reverse_transaction(
                user=read_only_user,
                original_transaction=dispose_txn,
                occurred_at=date.today(),
                reason="x",
            )


@pytest.mark.django_db
class TestQuantityReversalIntegrity:
    def test_reversing_quantity_transfer_restores_both_balances(
        self, administrator, quantity_product, location_tree, other_location_tree
    ):
        source = location_tree["room"]
        destination = other_location_tree["site"]
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=source,
            occurred_at=date.today(),
            quantity=10,
        )
        transfer = bulk_transfer(
            user=administrator,
            destination_location=destination,
            occurred_at=date.today(),
            quantity_lines=[
                {"product": quantity_product, "source_location": source, "quantity": 4}
            ],
        )
        reverse_transaction(
            user=administrator,
            original_transaction=transfer,
            occurred_at=date.today(),
            reason="wrong destination",
        )
        assert (
            StockBalance.objects.get(product=quantity_product, location=source).on_hand_quantity
            == 10
        )
        assert (
            StockBalance.objects.get(
                product=quantity_product, location=destination
            ).on_hand_quantity
            == 0
        )

    def test_reversing_quantity_reservation_restores_reserved_balance(
        self, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        reservation_txn = reserve_stock(
            user=administrator,
            occurred_at=date.today(),
            project_reference="PRJ-UNDO",
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 4}
            ],
        )
        reverse_transaction(
            user=administrator,
            original_transaction=reservation_txn,
            occurred_at=date.today(),
            reason="wrong project",
        )
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert balance.reserved_quantity == 0
