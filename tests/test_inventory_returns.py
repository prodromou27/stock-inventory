from datetime import date

import pytest
from django.core.exceptions import ValidationError

from apps.inventory.models import StockBalance, UnitAsset, UnitStatus
from apps.inventory.services.assignments import assign_to_employee
from apps.inventory.services.receipts import receive_stock
from apps.inventory.services.returns import assess_return, return_stock


@pytest.mark.django_db
class TestReturnStock:
    def test_quantity_returns_cannot_exceed_original_outstanding_amount(
        self, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        issue = assign_to_employee(
            user=administrator,
            employee_name="Sam",
            occurred_at=date.today(),
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 10}
            ],
        )
        return_stock(
            user=administrator,
            original_transaction=issue,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity_lines=[{"product": quantity_product, "quantity": 4}],
        )
        return_stock(
            user=administrator,
            original_transaction=issue,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity_lines=[{"product": quantity_product, "quantity": 6}],
        )

        with pytest.raises(ValidationError):
            return_stock(
                user=administrator,
                original_transaction=issue,
                location=location_tree["room"],
                occurred_at=date.today(),
                quantity_lines=[{"product": quantity_product, "quantity": 1}],
            )
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert balance.on_hand_quantity == 10

    def test_partial_return_leaves_other_lines_assigned(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-R1",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-R2",
        )
        asset1 = UnitAsset.objects.get(vendor_serial="SN-R1")
        asset2 = UnitAsset.objects.get(vendor_serial="SN-R2")
        assign_txn = assign_to_employee(
            user=administrator,
            employee_name="Henry",
            occurred_at=date.today(),
            unit_asset_ids=[asset1.pk, asset2.pk],
        )

        return_stock(
            user=administrator,
            original_transaction=assign_txn,
            location=location_tree["room"],
            occurred_at=date.today(),
            unit_asset_ids=[asset1.pk],
        )

        asset1.refresh_from_db()
        asset2.refresh_from_db()
        assert asset1.status == UnitStatus.RETURNED
        assert asset2.status == UnitStatus.ASSIGNED  # untouched

    def test_condition_and_accessories_captured_on_return(
        self, administrator, unit_product, location_tree
    ):
        """Regression coverage for acceptance criterion §21.8 ("Condition and
        accessories can be recorded at issue and return") — return_stock()
        already threaded these through to the line snapshot correctly, but
        had no dedicated test on the return side (found during the Prompt 9
        traceability audit).
        """
        from apps.inventory.models import Condition, InventoryTransactionLine

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-R-COND",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-R-COND")
        assign_txn = assign_to_employee(
            user=administrator,
            employee_name="Priya",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        return_txn = return_stock(
            user=administrator,
            original_transaction=assign_txn,
            location=location_tree["room"],
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            condition=Condition.FAIR,
            accessories="Charger missing",
        )

        line = InventoryTransactionLine.objects.get(transaction=return_txn, unit_asset=asset)
        assert line.condition_snapshot == Condition.FAIR
        assert line.accessories_snapshot == "Charger missing"

    def test_return_creates_new_transaction_linked_to_original(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-R3",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-R3")
        assign_txn = assign_to_employee(
            user=administrator,
            employee_name="Ivy",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        return_txn = return_stock(
            user=administrator,
            original_transaction=assign_txn,
            location=location_tree["room"],
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        assert return_txn.related_transaction == assign_txn
        assert return_txn.pk != assign_txn.pk
        # original transaction itself is untouched
        assert assign_txn.lines.count() == 1

    def test_cannot_return_asset_not_in_original_transaction(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-R4",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-R5",
        )
        asset4 = UnitAsset.objects.get(vendor_serial="SN-R4")
        asset5 = UnitAsset.objects.get(vendor_serial="SN-R5")
        assign_txn = assign_to_employee(
            user=administrator,
            employee_name="Jack",
            occurred_at=date.today(),
            unit_asset_ids=[asset4.pk],
        )

        with pytest.raises(ValidationError):
            return_stock(
                user=administrator,
                original_transaction=assign_txn,
                location=location_tree["room"],
                occurred_at=date.today(),
                unit_asset_ids=[asset5.pk],
            )

    def test_cannot_return_against_a_receipt_transaction(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-R6",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-R6")
        receipt_txn = asset.transaction_lines.first().transaction

        with pytest.raises(ValidationError):
            return_stock(
                user=administrator,
                original_transaction=receipt_txn,
                location=location_tree["room"],
                occurred_at=date.today(),
                unit_asset_ids=[asset.pk],
            )

    def test_quantity_return_goes_straight_to_on_hand(
        self, administrator, quantity_product, location_tree
    ):
        from apps.inventory.models import StockBalance

        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=20,
        )
        assign_txn = assign_to_employee(
            user=administrator,
            employee_name="Karen",
            occurred_at=date.today(),
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 5}
            ],
        )

        return_stock(
            user=administrator,
            original_transaction=assign_txn,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity_lines=[{"product": quantity_product, "quantity": 5}],
        )

        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert balance.on_hand_quantity == 20  # 20 - 5 assigned + 5 returned


@pytest.mark.django_db
class TestAssessReturn:
    def test_assessment_resolves_to_in_stock(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-AS1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-AS1")
        assign_txn = assign_to_employee(
            user=administrator,
            employee_name="Leo",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        return_stock(
            user=administrator,
            original_transaction=assign_txn,
            location=location_tree["room"],
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        assess_return(
            user=administrator,
            to_status=UnitStatus.IN_STOCK,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        asset.refresh_from_db()
        assert asset.status == UnitStatus.IN_STOCK

    def test_assessment_resolves_to_disposed(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-AS2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-AS2")
        assign_txn = assign_to_employee(
            user=administrator,
            employee_name="Mia",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        return_stock(
            user=administrator,
            original_transaction=assign_txn,
            location=location_tree["room"],
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        assess_return(
            user=administrator,
            to_status=UnitStatus.DISPOSED,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        asset.refresh_from_db()
        assert asset.status == UnitStatus.DISPOSED

    def test_cannot_assess_an_asset_that_is_not_returned(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-AS3",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-AS3")
        with pytest.raises(ValidationError):
            assess_return(
                user=administrator,
                to_status=UnitStatus.IN_STOCK,
                occurred_at=date.today(),
                unit_asset_ids=[asset.pk],
            )

    def test_assessment_target_must_be_valid_choice(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-AS4",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-AS4")
        assign_txn = assign_to_employee(
            user=administrator,
            employee_name="Nina",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        return_stock(
            user=administrator,
            original_transaction=assign_txn,
            location=location_tree["room"],
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        with pytest.raises(ValidationError):
            assess_return(
                user=administrator,
                to_status=UnitStatus.ASSIGNED,
                occurred_at=date.today(),
                unit_asset_ids=[asset.pk],
            )
