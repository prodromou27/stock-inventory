from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.audit.models import AuditEvent
from apps.inventory.models import MovementType, StockBalance, StockPurpose, UnitAsset
from apps.inventory.services.purpose import (
    reclassify_quantity_purpose,
    reclassify_unit_purpose,
)
from apps.inventory.services.receipts import receive_stock


@pytest.mark.django_db
class TestReclassifyUnitPurpose:
    def test_defaults_to_internal_on_receipt(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PURP-1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-PURP-1")
        assert asset.stock_purpose == StockPurpose.INTERNAL

    def test_can_receive_directly_as_customer_stock(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PURP-2",
            stock_purpose=StockPurpose.CUSTOMER,
            final_customer="Acme Co",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-PURP-2")
        assert asset.stock_purpose == StockPurpose.CUSTOMER

    def test_reclassify_changes_purpose_and_is_audited(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PURP-3",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-PURP-3")

        reclassify_unit_purpose(
            user=administrator,
            unit_asset=asset,
            new_purpose=StockPurpose.CUSTOMER,
            occurred_at=date.today(),
            reason="Earmarked for a customer order",
        )
        asset.refresh_from_db()
        assert asset.stock_purpose == StockPurpose.CUSTOMER
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.STOCK_PURPOSE_CHANGED
        ).exists()

    def test_reclassify_does_not_touch_status_or_write_a_ledger_transaction(
        self, administrator, unit_product, location_tree
    ):
        from apps.inventory.models import InventoryTransaction

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PURP-4",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-PURP-4")
        status_before = asset.status
        txn_count_before = InventoryTransaction.objects.count()

        reclassify_unit_purpose(
            user=administrator,
            unit_asset=asset,
            new_purpose=StockPurpose.CUSTOMER,
            occurred_at=date.today(),
            reason="Reclassified",
        )
        asset.refresh_from_db()
        assert asset.status == status_before
        assert InventoryTransaction.objects.count() == txn_count_before

    def test_same_purpose_rejected(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PURP-5",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-PURP-5")
        with pytest.raises(ValidationError):
            reclassify_unit_purpose(
                user=administrator,
                unit_asset=asset,
                new_purpose=StockPurpose.INTERNAL,
                occurred_at=date.today(),
                reason="No-op",
            )

    def test_reason_required(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PURP-6",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-PURP-6")
        with pytest.raises(ValidationError):
            reclassify_unit_purpose(
                user=administrator,
                unit_asset=asset,
                new_purpose=StockPurpose.CUSTOMER,
                occurred_at=date.today(),
                reason="",
            )

    def test_read_only_user_cannot_reclassify(self, read_only_user):
        # require_role() is checked before any DB lookup of the asset, so a
        # bogus pk is fine here — the permission check must fail first.
        with pytest.raises(PermissionDenied):
            reclassify_unit_purpose(
                user=read_only_user,
                unit_asset=UnitAsset(pk="00000000-0000-0000-0000-000000000000"),
                new_purpose=StockPurpose.CUSTOMER,
                occurred_at=date.today(),
                reason="denied",
            )


@pytest.mark.django_db
class TestReclassifyQuantityPurpose:
    def test_moves_quantity_between_buckets_atomically(
        self, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )

        txn = reclassify_quantity_purpose(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            from_purpose=StockPurpose.INTERNAL,
            to_purpose=StockPurpose.CUSTOMER,
            quantity=4,
            occurred_at=date.today(),
            reason="Reserved for customer order",
        )
        assert txn.movement_type == MovementType.PURPOSE_CHANGE

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
        assert internal.on_hand_quantity == 6
        assert customer.on_hand_quantity == 4

    def test_insufficient_stock_rejected_and_nothing_moved(
        self, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )
        with pytest.raises(ValidationError):
            reclassify_quantity_purpose(
                user=administrator,
                product=quantity_product,
                location=location_tree["room"],
                from_purpose=StockPurpose.INTERNAL,
                to_purpose=StockPurpose.CUSTOMER,
                quantity=10,
                occurred_at=date.today(),
                reason="Too much",
            )
        internal = StockBalance.objects.get(
            product=quantity_product,
            location=location_tree["room"],
            stock_purpose=StockPurpose.INTERNAL,
        )
        assert internal.on_hand_quantity == 3
        assert not StockBalance.objects.filter(
            product=quantity_product, stock_purpose=StockPurpose.CUSTOMER
        ).exists()

    def test_same_purpose_rejected(self, administrator, quantity_product, location_tree):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        with pytest.raises(ValidationError):
            reclassify_quantity_purpose(
                user=administrator,
                product=quantity_product,
                location=location_tree["room"],
                from_purpose=StockPurpose.INTERNAL,
                to_purpose=StockPurpose.INTERNAL,
                quantity=1,
                occurred_at=date.today(),
                reason="No-op",
            )

    def test_reserved_quantity_cannot_be_reclassified_away(
        self, administrator, quantity_product, location_tree
    ):
        from apps.inventory.services.reservations import reserve_stock

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
            project_reference="PROJ-RESV",
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 8}
            ],
        )
        # Only 2 available (10 on hand, 8 reserved) — reclassifying 5 must fail.
        with pytest.raises(ValidationError):
            reclassify_quantity_purpose(
                user=administrator,
                product=quantity_product,
                location=location_tree["room"],
                from_purpose=StockPurpose.INTERNAL,
                to_purpose=StockPurpose.CUSTOMER,
                quantity=5,
                occurred_at=date.today(),
                reason="Should fail",
            )
