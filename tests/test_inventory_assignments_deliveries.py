from datetime import date

import pytest
from django.core.exceptions import ValidationError

from apps.inventory.models import InventoryTransactionLine, StockBalance, UnitAsset, UnitStatus
from apps.inventory.services.assignments import assign_to_employee, deliver_to_customer
from apps.inventory.services.receipts import receive_stock


@pytest.mark.django_db
class TestAssignToEmployee:
    def test_unit_assignment_removes_from_storage(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-A1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-A1")

        txn = assign_to_employee(
            user=administrator,
            employee_name="Jane Doe",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        asset.refresh_from_db()
        assert asset.status == UnitStatus.ASSIGNED
        assert asset.current_location is None
        assert asset.last_removal_date == date.today()
        assert txn.employee_name == "Jane Doe"

    def test_mixed_multi_line_assignment(
        self, administrator, unit_product, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-A2",
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=20,
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-A2")

        txn = assign_to_employee(
            user=administrator,
            employee_name="Bob",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 3}
            ],
        )

        assert txn.lines.count() == 2
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert balance.on_hand_quantity == 17

    def test_condition_and_accessories_captured_on_line(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-A3",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-A3")

        txn = assign_to_employee(
            user=administrator,
            employee_name="Carl",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            condition="refurbished",
            accessories="charger, case",
        )
        line = InventoryTransactionLine.objects.get(transaction=txn)
        assert line.condition_snapshot == "refurbished"
        assert line.accessories_snapshot == "charger, case"
        asset.refresh_from_db()
        assert asset.condition == "refurbished"
        assert asset.accessories == "charger, case"

    def test_employee_name_required(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-A4",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-A4")
        with pytest.raises(ValidationError):
            assign_to_employee(
                user=administrator,
                employee_name="",
                occurred_at=date.today(),
                unit_asset_ids=[asset.pk],
            )

    def test_cannot_assign_already_assigned_asset(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-A5",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-A5")
        assign_to_employee(
            user=administrator,
            employee_name="Dan",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        with pytest.raises(ValidationError):
            assign_to_employee(
                user=administrator,
                employee_name="Eve",
                occurred_at=date.today(),
                unit_asset_ids=[asset.pk],
            )

    def test_cannot_assign_more_quantity_than_available(
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
            assign_to_employee(
                user=administrator,
                employee_name="Frank",
                occurred_at=date.today(),
                quantity_lines=[
                    {"product": quantity_product, "location": location_tree["room"], "quantity": 6}
                ],
            )

    def test_reserved_quantity_not_shippable_as_available(
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
            project_reference="PRJ-HOLD",
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 8}
            ],
        )
        # only 2 available (10 on hand - 8 reserved); assigning 3 must fail.
        with pytest.raises(ValidationError):
            assign_to_employee(
                user=administrator,
                employee_name="Grace",
                occurred_at=date.today(),
                quantity_lines=[
                    {"product": quantity_product, "location": location_tree["room"], "quantity": 3}
                ],
            )


@pytest.mark.django_db
class TestDeliverToCustomer:
    def test_unit_delivery_sets_delivered_status(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-D1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-D1")

        txn = deliver_to_customer(
            user=administrator,
            final_customer="Acme Corp",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        asset.refresh_from_db()
        assert asset.status == UnitStatus.DELIVERED
        assert asset.current_location is None
        assert txn.final_customer == "Acme Corp"

    def test_final_customer_required(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-D2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-D2")
        with pytest.raises(ValidationError):
            deliver_to_customer(
                user=administrator,
                final_customer="",
                occurred_at=date.today(),
                unit_asset_ids=[asset.pk],
            )

    def test_read_only_user_cannot_deliver(self, read_only_user, unit_product, location_tree):
        with pytest.raises(Exception):
            deliver_to_customer(
                user=read_only_user,
                final_customer="Acme",
                occurred_at=date.today(),
                unit_asset_ids=[],
            )

    def test_customer_reference_is_recorded_alongside_the_name_snapshot(
        self, administrator, unit_product, location_tree
    ):
        from apps.inventory.models import Customer

        customer = Customer.objects.create(name="Acme Corp", reference="ACME-01")
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-D3",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-D3")

        txn = deliver_to_customer(
            user=administrator,
            final_customer="Acme Corp",
            customer=customer,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        assert txn.customer_id == customer.pk
        assert txn.final_customer == "Acme Corp"

    def test_customer_reference_is_optional(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-D4",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-D4")

        txn = deliver_to_customer(
            user=administrator,
            final_customer="Some Walk-in Customer",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        assert txn.customer_id is None


@pytest.mark.django_db
class TestCurrentCustodyTracking:
    """UnitAsset.current_custody_transaction — the denormalized "who has
    this right now" pointer set/cleared by apps.inventory.services.ledger.
    write_unit_line() (new on direct request; see
    docs/architecture/09-delivery-backlog.md's dated entry for this wave).
    """

    def test_assignment_sets_custody_pointer_and_recipient_reference(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-CUST-1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-CUST-1")

        txn = assign_to_employee(
            user=administrator,
            employee_name="Jane Doe",
            recipient_reference="EMP-042",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        asset.refresh_from_db()
        assert asset.current_custody_transaction_id == txn.pk
        assert txn.recipient_reference == "EMP-042"

    def test_delivery_sets_custody_pointer(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-CUST-2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-CUST-2")

        txn = deliver_to_customer(
            user=administrator,
            final_customer="Acme Corp",
            recipient_reference="CUST-7",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        asset.refresh_from_db()
        assert asset.current_custody_transaction_id == txn.pk

    def test_custody_pointer_cleared_on_return(self, administrator, unit_product, location_tree):
        from apps.inventory.services.returns import return_stock

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-CUST-3",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-CUST-3")
        assignment_txn = assign_to_employee(
            user=administrator,
            employee_name="Jane Doe",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        asset.refresh_from_db()
        assert asset.current_custody_transaction_id == assignment_txn.pk

        return_stock(
            user=administrator,
            original_transaction=assignment_txn,
            location=location_tree["room"],
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        asset.refresh_from_db()
        assert asset.current_custody_transaction_id is None
        # History is untouched — the assignment transaction itself still exists unmodified.
        assert assignment_txn.employee_name == "Jane Doe"

    def test_mark_lost_directly_from_assigned_clears_custody(
        self, administrator, unit_product, location_tree
    ):
        from apps.inventory.services.disposition import mark_lost

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-CUST-4",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-CUST-4")
        assign_to_employee(
            user=administrator,
            employee_name="Jane Doe",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        asset.refresh_from_db()
        assert asset.current_custody_transaction_id is not None

        mark_lost(
            user=administrator,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            notes="Reported lost by employee",
        )
        asset.refresh_from_db()
        assert asset.status == UnitStatus.LOST
        assert asset.current_custody_transaction_id is None
