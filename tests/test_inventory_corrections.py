from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError

from apps.audit.models import AuditEvent
from apps.inventory.models import StockBalance, UnitAsset, UnitStatus
from apps.inventory.services.assignments import assign_to_employee
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
    def test_correction_rejects_status_location_inconsistency(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-CONSISTENCY",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-CONSISTENCY")
        with pytest.raises(ValidationError, match="cannot have a location"):
            correct_unit_status(
                user=administrator,
                unit_asset=asset,
                to_status=UnitStatus.DELIVERED,
                occurred_at=date.today(),
                reason="correct customer delivery",
                to_location=location_tree["room"],
            )
        asset.refresh_from_db()
        assert asset.status == UnitStatus.IN_STOCK

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

    def test_administrator_can_correct_arrival_date(
        self, administrator, unit_product, location_tree
    ):
        original_date = date.today()
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=original_date,
            vendor_serial="SN-ARRIVAL-CORRECT",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-ARRIVAL-CORRECT")
        corrected_date = original_date - timedelta(days=10)

        correct_unit_status(
            user=administrator,
            unit_asset=asset,
            to_status=asset.status,
            occurred_at=date.today(),
            reason="Arrival date was mis-entered",
            to_location=asset.current_location,
            arrival_date=corrected_date,
        )
        asset.refresh_from_db()
        assert asset.arrival_date == corrected_date

        event = AuditEvent.objects.filter(event_type=AuditEvent.EventType.ADMIN_CORRECTION).latest(
            "occurred_at"
        )
        assert event.old_values["arrival_date"] == original_date.isoformat()
        assert event.new_values["arrival_date"] == corrected_date.isoformat()

    def test_arrival_date_unaffected_when_correction_omits_it(
        self, administrator, unit_product, location_tree
    ):
        original_date = date.today()
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=original_date,
            vendor_serial="SN-ARRIVAL-UNCHANGED",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-ARRIVAL-UNCHANGED")

        correct_unit_status(
            user=administrator,
            unit_asset=asset,
            to_status=UnitStatus.DAMAGED,
            occurred_at=date.today(),
            reason="Found damaged",
        )
        asset.refresh_from_db()
        assert asset.arrival_date == original_date


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

    def test_reverses_purpose_change(self, administrator, quantity_product, location_tree):
        """A PURPOSE_CHANGE transaction has no dedicated branch in
        reverse_transaction() — it falls through to the generic quantity-line
        branch, which must still restore both buckets correctly since each
        line snapshots its own stock_purpose.
        """
        from apps.inventory.models import StockPurpose
        from apps.inventory.services.purpose import reclassify_quantity_purpose

        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        reclassify_txn = reclassify_quantity_purpose(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            from_purpose=StockPurpose.INTERNAL,
            to_purpose=StockPurpose.CUSTOMER,
            quantity=4,
            occurred_at=date.today(),
            reason="Reserved for customer order",
        )

        reverse_transaction(
            user=administrator,
            original_transaction=reclassify_txn,
            occurred_at=date.today(),
            reason="Reclassified in error",
        )

        internal = StockBalance.objects.get(
            product=quantity_product,
            location=location_tree["room"],
            stock_purpose=StockPurpose.INTERNAL,
        )
        customer = StockBalance.objects.get(
            product=quantity_product,
            location=location_tree["room"],
            stock_purpose=StockPurpose.CUSTOMER,
        )
        assert internal.on_hand_quantity == 10
        assert customer.on_hand_quantity == 0

    def test_reverses_a_customer_purpose_transfer(
        self, administrator, quantity_product, location_tree, other_location_tree
    ):
        """Confirms StockPurpose isn't just correctly reversed for the new
        PURPOSE_CHANGE type — an ordinary transfer done under Customer
        purpose must also reverse into the same (not Internal) bucket.
        """
        from apps.inventory.models import StockPurpose
        from apps.locations.models import Location
        from apps.locations.services import create_location

        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Reversal Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Reversal Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
            stock_purpose=StockPurpose.CUSTOMER,
            final_customer="Acme Co",
        )
        transfer_txn = bulk_transfer(
            user=administrator,
            destination_location=other_room,
            occurred_at=date.today(),
            quantity_lines=[
                {
                    "product": quantity_product,
                    "source_location": location_tree["room"],
                    "quantity": 6,
                    "stock_purpose": StockPurpose.CUSTOMER,
                }
            ],
        )

        reverse_transaction(
            user=administrator,
            original_transaction=transfer_txn,
            occurred_at=date.today(),
            reason="Wrong destination",
        )

        source = StockBalance.objects.get(
            product=quantity_product,
            location=location_tree["room"],
            stock_purpose=StockPurpose.CUSTOMER,
        )
        dest = StockBalance.objects.get(
            product=quantity_product, location=other_room, stock_purpose=StockPurpose.CUSTOMER
        )
        assert source.on_hand_quantity == 10
        assert dest.on_hand_quantity == 0

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
    def test_reversal_recomputes_last_removal_date(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date(2026, 1, 1),
            vendor_serial="SN-REMOVAL-REV",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-REMOVAL-REV")
        issue = assign_to_employee(
            user=administrator,
            employee_name="Morgan",
            occurred_at=date(2026, 2, 1),
            unit_asset_ids=[asset.pk],
        )
        reverse_transaction(
            user=administrator,
            original_transaction=issue,
            occurred_at=date(2026, 2, 2),
            reason="assignment entered in error",
        )
        asset.refresh_from_db()
        assert asset.last_removal_date is None

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

    def test_reversing_a_reservation_consuming_delivery_restores_on_hand_before_reserved(
        self, administrator, quantity_product, location_tree
    ):
        """Regression test: reversing an assignment/delivery that consumed a
        quantity reservation must restore on_hand *before* reserved, since
        reserved <= on_hand is a DB-enforced invariant — restoring reserved
        first (the original line_number order the ledger writes them in)
        would raise ValidationError forever, making the delivery
        permanently unreversible whenever the whole reservation was drawn
        down to (or past) the point where on_hand alone couldn't cover it.
        """
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
            project_reference="PRJ-REVERSE-DELIVERY",
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 8}
            ],
        )
        delivery_txn = assign_to_employee(
            user=administrator,
            employee_name="Someone",
            occurred_at=date.today(),
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 8}
            ],
            project_reference="PRJ-REVERSE-DELIVERY",
        )
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert balance.on_hand_quantity == 2
        assert balance.reserved_quantity == 0

        reverse_transaction(
            user=administrator,
            original_transaction=delivery_txn,
            occurred_at=date.today(),
            reason="wrong recipient",
        )

        balance.refresh_from_db()
        assert balance.on_hand_quantity == 10
        assert balance.reserved_quantity == 8
