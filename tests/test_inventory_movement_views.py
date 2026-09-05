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
from apps.inventory.services.disposition import dispose, mark_damaged
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
class TestMovementsHubContext:
    """apps.inventory.views._recent_transactions_for_hub()/
    _frequently_used_for_hub() — the Operations hub's "Recent transactions"
    and "Frequently used" panels.
    """

    def test_recent_transactions_lists_scoped_transactions(
        self, client, administrator, unit_product, location_tree
    ):
        txn = receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-HUB-RECENT",
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:movements_hub"))
        numbers = [t.transaction_number for t in response.context["recent_transactions"]]
        assert txn.transaction_number in numbers

    def test_frequently_used_ranks_products_by_line_count(
        self, client, administrator, unit_product, quantity_product, location_tree
    ):
        for i in range(3):
            receive_stock(
                user=administrator,
                product=unit_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                vendor_serial=f"SN-HUB-FREQ-{i}",
            )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:movements_hub"))
        labels = [p["label"] for p in response.context["frequent_products"]]
        assert labels.index(str(unit_product)) < labels.index(str(quantity_product))

    def test_frequently_used_locations_scoped_to_accessible_locations(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        location_tree,
        other_location_tree,
    ):
        from apps.locations.models import Location
        from apps.locations.services import create_location

        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Hub Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Hub Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-HUB-OUT-OF-SCOPE",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-HUB-IN-SCOPE",
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:movements_hub"))
        location_names = [
            entry["location"].name for entry in response.context["frequent_locations"]
        ]
        assert "Hub Room" not in location_names
        assert location_tree["room"].name in location_names


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

    def test_resubmitting_the_same_submission_token_does_not_transfer_twice(
        self, client, stock_manager_with_room_access, unit_product, location_tree, rack
    ):
        from apps.inventory.models import InventoryTransaction

        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-TV-DUPTOKEN",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-TV-DUPTOKEN")

        client.force_login(stock_manager_with_room_access)
        get_response = client.get(reverse("inventory:transfer"))
        token = get_response.context["form"]["submission_token"].value()
        assert token

        payload = {
            "destination_location": rack.pk,
            "occurred_at": date.today().isoformat(),
            "unit_asset_ids": [str(asset.pk)],
            "submission_token": token,
        }
        first = client.post(reverse("inventory:transfer"), payload)
        assert first.status_code == 302

        second = client.post(reverse("inventory:transfer"), payload)
        assert second.status_code == 302
        assert second.url == reverse("inventory:movements_hub")
        assert InventoryTransaction.objects.filter(movement_type="transfer").count() == 1

    def test_manipulated_destination_location_outside_scope_rejected(
        self,
        client,
        stock_manager_with_room_access,
        administrator,
        unit_product,
        location_tree,
        other_location_tree,
    ):
        """A tampered POST naming a real Location the operator has no
        access to must be rejected, not silently honored — destination_location
        is a ModelChoiceField whose queryset is already scoped
        (apps.inventory.forms._apply_scoped_location), so Django's own
        "not a valid choice" validation is the enforcement mechanism here.
        """
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
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-TV-XCOUNTRY",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-TV-XCOUNTRY")

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:transfer"),
            {
                "destination_location": other_room.pk,
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
            },
        )
        assert response.status_code == 200
        assert "valid choice" in response.content.decode()
        asset.refresh_from_db()
        assert asset.current_location == location_tree["room"]

    def test_transfers_multiple_quantity_rows_in_one_transaction(
        self, client, stock_manager_with_room_access, quantity_product, location_tree, rack
    ):
        import json

        from apps.inventory.models import InventoryTransactionLine, StockPurpose

        for purpose in (StockPurpose.INTERNAL, StockPurpose.CUSTOMER):
            receive_stock(
                user=stock_manager_with_room_access,
                product=quantity_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                quantity=10,
                stock_purpose=purpose,
            )
        balances = list(
            StockBalance.objects.filter(product=quantity_product).order_by("stock_purpose")
        )
        client.force_login(stock_manager_with_room_access)

        response = client.post(
            reverse("inventory:transfer"),
            {
                "destination_location": rack.pk,
                "occurred_at": date.today().isoformat(),
                "quantity_lines_json": json.dumps(
                    [
                        {"balance_id": str(balances[0].pk), "quantity": 2},
                        {"balance_id": str(balances[1].pk), "quantity": 3},
                    ]
                ),
            },
        )

        assert response.status_code == 302
        assert (
            InventoryTransactionLine.objects.filter(
                transaction__movement_type="transfer", product=quantity_product
            ).count()
            == 2
        )


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

    def test_reserves_multiple_quantity_rows_in_one_transaction(
        self, client, stock_manager_with_room_access, quantity_product, location_tree
    ):
        import json

        from apps.inventory.models import StockPurpose

        for purpose in (StockPurpose.INTERNAL, StockPurpose.CUSTOMER):
            receive_stock(
                user=stock_manager_with_room_access,
                product=quantity_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                quantity=10,
                stock_purpose=purpose,
            )
        balances = list(
            StockBalance.objects.filter(product=quantity_product).order_by("stock_purpose")
        )
        client.force_login(stock_manager_with_room_access)

        response = client.post(
            reverse("inventory:reserve"),
            {
                "occurred_at": date.today().isoformat(),
                "project_reference": "PRJ-MULTI-QTY",
                "quantity_lines_json": json.dumps(
                    [
                        {"balance_id": str(balances[0].pk), "quantity": 2},
                        {"balance_id": str(balances[1].pk), "quantity": 3},
                    ]
                ),
            },
        )

        assert response.status_code == 302
        assert StockReservation.objects.filter(project_reference="PRJ-MULTI-QTY").count() == 2


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

    def test_deliver_quantity_via_balance_picker(
        self, client, stock_manager_with_room_access, quantity_product, location_tree
    ):
        import json

        receive_stock(
            user=stock_manager_with_room_access,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=10,
        )
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:deliver"),
            {
                "final_customer": "Acme Balance Corp",
                "occurred_at": date.today().isoformat(),
                "quantity_lines_json": json.dumps([{"balance_id": str(balance.pk), "quantity": 4}]),
            },
        )
        assert response.status_code == 302
        balance.refresh_from_db()
        assert balance.on_hand_quantity == 6

    def test_deliver_quantity_capped_at_available(
        self, client, stock_manager_with_room_access, quantity_product, location_tree
    ):
        import json

        receive_stock(
            user=stock_manager_with_room_access,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=5,
        )
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:deliver"),
            {
                "final_customer": "Acme Overdraw Corp",
                "occurred_at": date.today().isoformat(),
                "quantity_lines_json": json.dumps(
                    [{"balance_id": str(balance.pk), "quantity": 999}]
                ),
            },
        )
        assert response.status_code == 200
        assert "999" in response.content.decode() or "Only 5" in response.content.decode()
        balance.refresh_from_db()
        assert balance.on_hand_quantity == 5

    def test_deliver_manipulated_balance_id_outside_scope_rejected(
        self,
        client,
        stock_manager_with_room_access,
        administrator,
        quantity_product,
        other_location_tree,
    ):
        """A tampered quantity_lines_json naming a real StockBalance the
        operator has no location access to must be rejected server-side —
        _quantity_lines_from_balance_picker() calls require_location_access()
        on every resolved balance's location, independent of whatever the
        picker grid itself would have offered.
        """
        import json

        from apps.locations.models import Location
        from apps.locations.services import create_location

        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Other Deliver Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Other Deliver Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=other_room,
            occurred_at=date.today(),
            quantity=10,
        )
        balance = StockBalance.objects.get(product=quantity_product, location=other_room)

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:deliver"),
            {
                "final_customer": "Acme XCountry Corp",
                "occurred_at": date.today().isoformat(),
                "quantity_lines_json": json.dumps([{"balance_id": str(balance.pk), "quantity": 3}]),
            },
        )
        assert response.status_code == 403
        balance.refresh_from_db()
        assert balance.on_hand_quantity == 10

    def test_deliver_no_location_or_purpose_field_is_exposed(
        self, client, stock_manager_with_room_access
    ):
        """Per direct instruction: Assign/Deliver ask only for items, date,
        and recipient — location and stock purpose are implicit in which
        grid row was picked, never a separate dropdown.
        """
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:deliver"))
        assert "quantity_location" not in response.content.decode()
        assert 'name="quantity_stock_purpose"' not in response.content.decode()


@pytest.mark.django_db
class TestBalancePickerDataView:
    def test_lists_eligible_balances(
        self, client, stock_manager_with_room_access, quantity_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=7,
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:balance_picker_data"))
        rows = response.json()["data"]
        assert any(row["available"] == 7 for row in rows)

    def test_zero_available_balance_excluded(
        self, client, stock_manager_with_room_access, quantity_product, location_tree
    ):
        from apps.inventory.services.reservations import reserve_stock

        receive_stock(
            user=stock_manager_with_room_access,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )
        reserve_stock(
            user=stock_manager_with_room_access,
            occurred_at=date.today(),
            project_reference="Reserved for test",
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 3}
            ],
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:balance_picker_data"))
        rows = response.json()["data"]
        assert not any(row["id"] for row in rows if row.get("available", 1) == 0)


@pytest.mark.django_db
class TestReturnAndAssessViews:
    def test_return_view_supports_multiple_quantity_lines(
        self, client, stock_manager_with_room_access, quantity_product, location_tree
    ):
        from apps.catalog.models import Brand, Product

        second_product = Product.objects.create(
            brand=Brand.objects.create(name="Return view brand"),
            product_type=quantity_product.product_type,
            model="Return view second product",
            sku="RETURN-VIEW-SECOND",
            category=quantity_product.category,
            tracking_method=quantity_product.tracking_method,
        )
        for product in (quantity_product, second_product):
            receive_stock(
                user=stock_manager_with_room_access,
                product=product,
                location=location_tree["room"],
                occurred_at=date.today(),
                quantity=10,
            )
        issue = assign_to_employee(
            user=stock_manager_with_room_access,
            employee_name="Multiple quantity recipient",
            occurred_at=date.today(),
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 4},
                {"product": second_product, "location": location_tree["room"], "quantity": 6},
            ],
        )

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:return_stock", kwargs={"pk": issue.pk}),
            {
                "location": location_tree["room"].pk,
                "occurred_at": date.today().isoformat(),
                f"return_quantity__{quantity_product.pk}__internal": "2",
                f"return_quantity__{second_product.pk}__internal": "3",
            },
        )

        assert response.status_code == 302
        first_balance = StockBalance.objects.get(
            product=quantity_product, location=location_tree["room"]
        )
        second_balance = StockBalance.objects.get(
            product=second_product, location=location_tree["room"]
        )
        assert first_balance.on_hand_quantity == 8
        assert second_balance.on_hand_quantity == 7
        returned_lines = issue.related_transactions.get().lines.filter(unit_asset=None)
        assert sorted(returned_lines.values_list("quantity_delta", flat=True)) == [2, 3]

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

    def test_mark_lost_without_acknowledgement_is_rejected(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-MLV-NOACK",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-MLV-NOACK")

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:mark_lost"),
            {
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
                "notes": "missing",
            },
        )
        assert response.status_code == 200
        assert "acknowledged" in response.context["form"].errors
        asset.refresh_from_db()
        assert asset.status == UnitStatus.IN_STOCK

    def test_dispose_without_acknowledgement_is_rejected(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DPV-NOACK",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DPV-NOACK")

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:dispose"),
            {
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
                "notes": "eol",
                "wipe_method": "software_wipe",
            },
        )
        assert response.status_code == 200
        assert "acknowledged" in response.context["form"].errors
        asset.refresh_from_db()
        assert asset.status == UnitStatus.IN_STOCK

    def test_mark_damaged_has_no_acknowledgement_field(
        self, client, stock_manager_with_room_access
    ):
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:mark_damaged"))
        assert "acknowledged" not in response.context["form"].fields

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
                "acknowledged": "true",
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
                "wipe_method": "software_wipe",
                "acknowledged": "true",
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == UnitStatus.DISPOSED

    def test_marks_multiple_quantity_rows_damaged_in_one_transaction(
        self, client, stock_manager_with_room_access, quantity_product, location_tree
    ):
        import json

        from apps.inventory.models import InventoryTransactionLine, StockPurpose

        for purpose in (StockPurpose.INTERNAL, StockPurpose.CUSTOMER):
            receive_stock(
                user=stock_manager_with_room_access,
                product=quantity_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                quantity=10,
                stock_purpose=purpose,
            )
        balances = list(
            StockBalance.objects.filter(product=quantity_product).order_by("stock_purpose")
        )
        client.force_login(stock_manager_with_room_access)

        response = client.post(
            reverse("inventory:mark_damaged"),
            {
                "occurred_at": date.today().isoformat(),
                "notes": "Batch damage",
                "quantity_lines_json": json.dumps(
                    [
                        {"balance_id": str(balances[0].pk), "quantity": 2},
                        {"balance_id": str(balances[1].pk), "quantity": 3},
                    ]
                ),
            },
        )

        assert response.status_code == 302
        assert (
            InventoryTransactionLine.objects.filter(
                transaction__movement_type="mark_damaged", product=quantity_product
            ).count()
            == 2
        )

    def test_dispose_view_requires_wipe_method(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DPV-NOWIPE",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DPV-NOWIPE")

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:dispose"),
            {
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
                "notes": "eol",
            },
        )
        assert response.status_code == 200
        assert "wipe_method" in response.context["form"].errors
        asset.refresh_from_db()
        assert asset.status != UnitStatus.DISPOSED

    def test_dispose_view_stores_witness_name(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        from apps.inventory.models import InventoryTransaction

        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DPV-WITNESS",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DPV-WITNESS")

        client.force_login(stock_manager_with_room_access)
        client.post(
            reverse("inventory:dispose"),
            {
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
                "notes": "eol",
                "wipe_method": "degaussed",
                "witness_name": "J. Alvarez",
                "acknowledged": "true",
            },
        )
        txn = InventoryTransaction.objects.get(movement_type="disposal")
        assert txn.wipe_method == "degaussed"
        assert txn.witness_name == "J. Alvarez"

    def test_read_only_cannot_mark_damaged(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("inventory:mark_damaged"))
        assert response.status_code == 403

    def test_stock_manager_can_complete_repair_view(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-REPAIR-VIEW",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-REPAIR-VIEW")
        mark_damaged(
            user=stock_manager_with_room_access,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            notes="failed fan",
        )
        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:repair_damaged"),
            {
                "location": str(location_tree["room"].pk),
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(asset.pk)],
                "notes": "fan replaced",
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == UnitStatus.IN_STOCK


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
