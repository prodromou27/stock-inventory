from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.audit.models import AuditEvent
from apps.inventory.models import (
    AssetStatusHistory,
    InventoryTransaction,
    InventoryTransactionLine,
    MovementType,
    StockBalance,
    UnitAsset,
    UnitStatus,
)
from apps.inventory.services.duplicates import check_duplicate_serial
from apps.inventory.services.receipts import (
    DuplicateSerialError,
    receive_stock,
    receive_stock_batch,
)


@pytest.mark.django_db
class TestReceiveUnitStock:
    def test_out_of_scope_duplicate_still_requires_acknowledgement_without_leaking_match(
        self,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        location_tree,
        other_location_tree,
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_location_tree["site"],
            occurred_at=date.today(),
            vendor_serial="SN-GLOBAL-DUP",
        )
        with pytest.raises(DuplicateSerialError) as exc_info:
            receive_stock(
                user=stock_manager_with_room_access,
                product=unit_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                vendor_serial="SN-GLOBAL-DUP",
            )
        assert exc_info.value.matches == []

    def test_quantity_ledger_rejects_unit_tracked_product(
        self, administrator, unit_product, location_tree
    ):
        from apps.inventory.services.ledger import adjust_balance

        with pytest.raises(ValidationError):
            adjust_balance(product=unit_product, location=location_tree["room"], delta=1)
        assert not StockBalance.objects.filter(product=unit_product).exists()

    def test_receipt_creates_unit_asset_in_stock(self, administrator, unit_product, location_tree):
        txn = receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-1",
        )

        asset = UnitAsset.objects.get(product=unit_product, vendor_serial="SN-1")
        assert asset.status == UnitStatus.IN_STOCK
        assert asset.current_location == location_tree["room"]
        assert txn.movement_type == MovementType.RECEIPT
        assert txn.transaction_number.startswith("TXN-")

    def test_receipt_writes_one_transaction_line_with_snapshots(
        self, administrator, unit_product, location_tree
    ):
        txn = receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-2",
        )

        line = InventoryTransactionLine.objects.get(transaction=txn)
        assert line.quantity_delta == 1
        assert line.brand_snapshot == unit_product.brand.name
        assert line.model_snapshot == unit_product.model
        assert line.serial_snapshot == "SN-2"
        assert line.to_status == UnitStatus.IN_STOCK

    def test_receipt_writes_asset_status_history(self, administrator, unit_product, location_tree):
        txn = receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-3",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-3")

        history = AssetStatusHistory.objects.get(unit_asset=asset)
        assert history.from_status is None
        assert history.to_status == UnitStatus.IN_STOCK
        assert history.transaction == txn

    def test_receipt_is_audited(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-4",
        )

        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.MOVEMENT_COMPLETED
        ).exists()

    def test_duplicate_serial_blocks_without_acknowledgement(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DUP",
        )

        with pytest.raises(DuplicateSerialError) as exc_info:
            receive_stock(
                user=administrator,
                product=unit_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                vendor_serial="SN-DUP",
            )
        assert len(exc_info.value.matches) == 1
        # the failed attempt must not have created a second asset or transaction
        assert UnitAsset.objects.filter(vendor_serial="SN-DUP").count() == 1

    def test_duplicate_serial_allowed_with_acknowledgement_and_audited(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DUP-2",
        )
        txn = receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DUP-2",
            duplicate_serial_acknowledged=True,
        )

        assert UnitAsset.objects.filter(vendor_serial="SN-DUP-2").count() == 2
        assert txn.duplicate_serial_acknowledged is True
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.DUPLICATE_SERIAL_ACKNOWLEDGED
        ).exists()

    def test_duplicate_serial_check_is_scoped_to_user_access(
        self,
        stock_manager_with_room_access,
        administrator,
        unit_product,
        location_tree,
        other_location_tree,
    ):
        from apps.locations.models import Location
        from apps.locations.services import create_location

        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Other Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Other Room",
            parent=other_floor,
            user=administrator,
        )
        # Administrator receives the same serial in a location the scoped
        # stock manager cannot see.
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-SCOPED",
        )

        matches = check_duplicate_serial("SN-SCOPED", user=stock_manager_with_room_access)
        assert matches.count() == 0

    def test_serials_are_allowed_to_repeat_no_db_uniqueness(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-REPEAT",
        )
        txn = receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-REPEAT",
            duplicate_serial_acknowledged=True,
        )
        assert txn is not None  # no IntegrityError raised

    def test_scope_enforced_stock_manager_without_access_denied(
        self, stock_manager, unit_product, location_tree
    ):
        with pytest.raises(PermissionDenied):
            receive_stock(
                user=stock_manager,
                product=unit_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                vendor_serial="SN-DENIED",
            )
        assert not UnitAsset.objects.filter(vendor_serial="SN-DENIED").exists()

    def test_read_only_user_cannot_receive_stock(self, read_only_user, unit_product, location_tree):
        with pytest.raises(Exception):
            receive_stock(
                user=read_only_user,
                product=unit_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                vendor_serial="SN-RO",
            )

    def test_cannot_receive_inactive_product(self, administrator, unit_product, location_tree):
        unit_product.is_active = False
        unit_product.save(update_fields=["is_active"])

        with pytest.raises(ValidationError):
            receive_stock(
                user=administrator,
                product=unit_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                vendor_serial="SN-INACTIVE",
            )


@pytest.mark.django_db
class TestReceiveQuantityStock:
    def test_receipt_increments_stock_balance(self, administrator, quantity_product, location_tree):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )

        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert balance.on_hand_quantity == 10
        assert balance.reserved_quantity == 0
        assert balance.available_quantity == 10

    def test_second_receipt_accumulates_on_same_balance_row(
        self, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=5,
        )

        assert (
            StockBalance.objects.filter(
                product=quantity_product, location=location_tree["room"]
            ).count()
            == 1
        )
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert balance.on_hand_quantity == 15

    def test_zero_or_negative_quantity_rejected(
        self, administrator, quantity_product, location_tree
    ):
        with pytest.raises(ValidationError):
            receive_stock(
                user=administrator,
                product=quantity_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                quantity=0,
            )
        assert not StockBalance.objects.filter(product=quantity_product).exists()

    def test_receipt_writes_signed_delta_line(self, administrator, quantity_product, location_tree):
        txn = receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=7,
        )

        line = InventoryTransactionLine.objects.get(transaction=txn)
        assert line.unit_asset is None
        assert line.quantity_delta == 7


@pytest.mark.django_db
class TestLedgerImmutability:
    def test_transaction_cannot_be_updated_after_creation(
        self, administrator, unit_product, location_tree
    ):
        txn = receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-IMMUTABLE",
        )
        txn.notes = "tampered"
        with pytest.raises(ValueError):
            txn.save()

    def test_transaction_cannot_be_deleted(self, administrator, unit_product, location_tree):
        txn = receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-IMMUTABLE-2",
        )
        with pytest.raises(ValueError):
            txn.delete()

    def test_transaction_line_cannot_be_updated(self, administrator, unit_product, location_tree):
        txn = receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-IMMUTABLE-3",
        )
        line = InventoryTransactionLine.objects.get(transaction=txn)
        line.notes = "tampered"
        with pytest.raises(ValueError):
            line.save()

    def test_bulk_update_and_delete_blocked_on_transactions(self):
        with pytest.raises(ValueError):
            InventoryTransaction.objects.all().update(notes="x")
        with pytest.raises(ValueError):
            InventoryTransaction.objects.all().delete()


@pytest.mark.django_db
class TestReceiveStockBatch:
    """apps.inventory.views.QuickReceiveView's service — one product/location,
    many serials, each its own receive_stock() call.
    """

    def test_creates_one_unit_asset_per_serial(self, administrator, unit_product, location_tree):
        results = receive_stock_batch(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serials=["SN-BATCH-1", "SN-BATCH-2", "SN-BATCH-3"],
        )
        assert [r["status"] for r in results] == ["created", "created", "created"]
        assert UnitAsset.objects.filter(vendor_serial__startswith="SN-BATCH-").count() == 3

    def test_blank_lines_are_ignored(self, administrator, unit_product, location_tree):
        results = receive_stock_batch(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serials=["SN-BATCH-BLANK-1", "", "   ", "SN-BATCH-BLANK-2"],
        )
        assert len(results) == 2
        assert all(r["status"] == "created" for r in results)

    def test_repeated_serial_within_the_same_batch_is_flagged_not_duplicated(
        self, administrator, unit_product, location_tree
    ):
        results = receive_stock_batch(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serials=["SN-BATCH-REPEAT", "SN-BATCH-REPEAT"],
        )
        assert results[0]["status"] == "created"
        assert results[1]["status"] == "skipped_repeat_in_batch"
        assert UnitAsset.objects.filter(vendor_serial="SN-BATCH-REPEAT").count() == 1

    def test_serial_matching_a_pre_existing_asset_is_reported_as_duplicate_not_raised(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-BATCH-EXISTING",
        )

        results = receive_stock_batch(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serials=["SN-BATCH-NEW", "SN-BATCH-EXISTING"],
        )
        assert results[0]["status"] == "created"
        assert results[1]["status"] == "duplicate"
        # Still exactly one asset for the pre-existing serial — never
        # auto-acknowledged/auto-created on top of it.
        assert UnitAsset.objects.filter(vendor_serial="SN-BATCH-EXISTING").count() == 1

    def test_one_bad_row_does_not_block_the_rest_of_the_batch(
        self, administrator, unit_product, location_tree, other_location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-BATCH-ALREADYTHERE",
        )
        results = receive_stock_batch(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serials=["SN-BATCH-BEFORE", "SN-BATCH-ALREADYTHERE", "SN-BATCH-AFTER"],
        )
        assert [r["status"] for r in results] == ["created", "duplicate", "created"]

    def test_rejects_quantity_tracked_products(
        self, administrator, quantity_product, location_tree
    ):
        with pytest.raises(ValidationError, match="serialized"):
            receive_stock_batch(
                user=administrator,
                product=quantity_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                vendor_serials=["SN-1"],
            )

    def test_requires_role(self, read_only_user, unit_product, location_tree):
        with pytest.raises(PermissionDenied):
            receive_stock_batch(
                user=read_only_user,
                product=unit_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                vendor_serials=["SN-1"],
            )

    def test_enforces_location_scope_once_not_per_row(
        self, stock_manager_with_room_access, unit_product, other_location_tree
    ):
        with pytest.raises(PermissionDenied):
            receive_stock_batch(
                user=stock_manager_with_room_access,
                product=unit_product,
                location=other_location_tree["site"],
                occurred_at=date.today(),
                vendor_serials=["SN-1", "SN-2", "SN-3"],
            )
        assert not UnitAsset.objects.filter(vendor_serial__in=["SN-1", "SN-2", "SN-3"]).exists()

    def test_shared_fields_applied_to_every_row(self, administrator, unit_product, location_tree):
        results = receive_stock_batch(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serials=["SN-BATCH-META-1", "SN-BATCH-META-2"],
            project_reference="PROJ-42",
            final_customer="Acme Co",
        )
        for row in results:
            asset = UnitAsset.objects.get(vendor_serial=row["serial"])
            assert asset.project_reference == "PROJ-42"
            assert asset.final_customer == "Acme Co"
