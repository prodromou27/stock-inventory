from datetime import date

import pytest
from django.urls import reverse

from apps.inventory.models import (
    ReservationStatus,
    StockBalance,
    StockReservation,
    UnitAsset,
    UnitStatus,
)
from apps.inventory.services.assignments import assign_to_employee
from apps.inventory.services.disposition import dispose
from apps.inventory.services.receipts import receive_stock
from apps.inventory.services.reservations import reserve_stock


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
class TestMovementsHubAccess:
    def test_anonymous_redirected(self, client):
        response = client.get(reverse("inventory:movements_hub"))
        assert response.status_code == 302

    def test_read_only_forbidden(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("inventory:movements_hub"))
        assert response.status_code == 403

    def test_stock_manager_allowed(self, client, stock_manager_with_room_access):
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:movements_hub"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestTransferView:
    def test_full_flow(
        self, client, stock_manager_with_room_access, unit_product, location_tree, rack
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-TV1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-TV1")

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:transfer"),
            {
                "destination_location": rack.pk,
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.current_location == rack

    def test_read_only_forbidden(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("inventory:transfer"))
        assert response.status_code == 403


@pytest.mark.django_db
class TestReserveAndReleaseViews:
    def test_reserve_full_flow(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-RV1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-RV1")

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:reserve"),
            {
                "occurred_at": date.today().isoformat(),
                "project_reference": "PRJ-UI-1",
                "unit_asset_ids": [str(asset.pk)],
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == UnitStatus.RESERVED

    def test_release_reservation_view(
        self, client, stock_manager_with_room_access, administrator, quantity_product, location_tree
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
            project_reference="PRJ-UI-2",
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 3}
            ],
        )
        reservation = StockReservation.objects.get(product=quantity_product)

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:release_reservation", kwargs={"pk": reservation.pk})
        )
        assert response.status_code == 302
        reservation.refresh_from_db()
        assert reservation.status == ReservationStatus.RELEASED

    def test_reservation_list_view_scoped(
        self, client, stock_manager_with_room_access, quantity_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        reserve_stock(
            user=stock_manager_with_room_access,
            occurred_at=date.today(),
            project_reference="PRJ-UI-3",
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 2}
            ],
        )

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:reservation_list"))
        assert response.status_code == 200
        assert len(response.context["reservations"]) == 1


@pytest.mark.django_db
class TestAssignAndDeliverViews:
    def test_assign_full_flow(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-AV1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-AV1")

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:assign"),
            {
                "employee_name": "Olga",
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == UnitStatus.ASSIGNED

    def test_deliver_full_flow(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DV1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DV1")

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:deliver"),
            {
                "final_customer": "Acme UI Corp",
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == UnitStatus.DELIVERED


@pytest.mark.django_db
class TestReturnAndAssessViews:
    def test_return_view_full_flow(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-RTV1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-RTV1")
        assign_txn = assign_to_employee(
            user=stock_manager_with_room_access,
            employee_name="Pete",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:return_stock", kwargs={"pk": assign_txn.pk}),
            {
                "location": location_tree["room"].pk,
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == UnitStatus.RETURNED

    def test_assess_return_view_full_flow(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-ASV1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-ASV1")
        assign_txn = assign_to_employee(
            user=stock_manager_with_room_access,
            employee_name="Quinn",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        from apps.inventory.services.returns import return_stock

        return_stock(
            user=stock_manager_with_room_access,
            original_transaction=assign_txn,
            location=location_tree["room"],
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:assess_return"),
            {
                "to_status": UnitStatus.IN_STOCK,
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == UnitStatus.IN_STOCK


@pytest.mark.django_db
class TestDispositionViews:
    def test_mark_damaged_view(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-MDV1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-MDV1")

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:mark_damaged"),
            {
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
                "notes": "dropped",
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == UnitStatus.DAMAGED

    def test_mark_lost_view(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-MLV1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-MLV1")

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:mark_lost"),
            {
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
                "notes": "missing",
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == UnitStatus.LOST

    def test_dispose_view(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DPV1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DPV1")

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:dispose"),
            {
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
                "notes": "eol",
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == UnitStatus.DISPOSED

    def test_read_only_cannot_mark_damaged(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("inventory:mark_damaged"))
        assert response.status_code == 403


@pytest.mark.django_db
class TestAdminCorrectionAndReversalViews:
    def test_stock_manager_cannot_access_correction_view(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-CV1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-CV1")

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:asset_correct", kwargs={"pk": asset.pk}))
        assert response.status_code == 403

    def test_admin_correct_unit_view(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-CV2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-CV2")

        client.force_login(administrator)
        response = client.post(
            reverse("inventory:asset_correct", kwargs={"pk": asset.pk}),
            {
                "to_status": UnitStatus.DAMAGED,
                "occurred_at": date.today().isoformat(),
                "reason": "found damaged during audit",
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == UnitStatus.DAMAGED

    def test_admin_correct_balance_view(
        self, client, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])

        client.force_login(administrator)
        response = client.post(
            reverse("inventory:balance_correct", kwargs={"pk": balance.pk}),
            {
                "new_on_hand_quantity": 25,
                "occurred_at": date.today().isoformat(),
                "reason": "count discrepancy",
            },
        )
        assert response.status_code == 302
        balance.refresh_from_db()
        assert balance.on_hand_quantity == 25

    def test_admin_reverse_view(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-RVV1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-RVV1")
        dispose_txn = dispose(
            user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk], notes="mistake"
        )

        client.force_login(administrator)
        response = client.post(
            reverse("inventory:transaction_reverse", kwargs={"pk": dispose_txn.pk}),
            {"occurred_at": date.today().isoformat(), "reason": "disposed by mistake"},
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == UnitStatus.IN_STOCK

    def test_read_only_cannot_reverse(
        self, client, administrator, read_only_user, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-RVV2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-RVV2")
        dispose_txn = dispose(
            user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk], notes="x"
        )

        client.force_login(read_only_user)
        response = client.get(
            reverse("inventory:transaction_reverse", kwargs={"pk": dispose_txn.pk})
        )
        assert response.status_code == 403
