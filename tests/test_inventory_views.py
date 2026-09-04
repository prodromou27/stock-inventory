from datetime import date

import pytest
from django.urls import reverse

from apps.inventory.models import StockBalance, UnitAsset
from apps.inventory.services.receipts import receive_stock


@pytest.mark.django_db
class TestReceiveStockView:
    def test_anonymous_redirected_to_login(self, client):
        response = client.get(reverse("inventory:receive_stock"))
        assert response.status_code == 302

    def test_read_only_user_forbidden(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("inventory:receive_stock"))
        assert response.status_code == 403

    def test_stock_manager_can_receive_unit_stock(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:receive_stock"),
            {
                "product": unit_product.pk,
                "location": location_tree["room"].pk,
                "occurred_at": date.today().isoformat(),
                "vendor_serial": "SN-VIEW-1",
            },
        )
        assert response.status_code == 302
        assert UnitAsset.objects.filter(vendor_serial="SN-VIEW-1").exists()

    def test_stock_manager_can_receive_quantity_stock(
        self, client, stock_manager_with_room_access, quantity_product, location_tree
    ):
        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:receive_stock"),
            {
                "product": quantity_product.pk,
                "location": location_tree["room"].pk,
                "occurred_at": date.today().isoformat(),
                "quantity": 15,
            },
        )
        assert response.status_code == 302
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
        assert balance.on_hand_quantity == 15

    def test_location_field_only_offers_accessible_locations(
        self, client, stock_manager_with_room_access, location_tree, other_location_tree
    ):
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:receive_stock"))
        location_choices = list(response.context["form"].fields["location"].queryset)
        assert location_tree["room"] in location_choices
        assert other_location_tree["country"] not in location_choices

    def test_duplicate_serial_shows_warning_page(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-VIEW-DUP",
        )

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:receive_stock"),
            {
                "product": unit_product.pk,
                "location": location_tree["room"].pk,
                "occurred_at": date.today().isoformat(),
                "vendor_serial": "SN-VIEW-DUP",
            },
        )
        assert response.status_code == 200
        assert response.context["show_duplicate_warning"] is True
        assert UnitAsset.objects.filter(vendor_serial="SN-VIEW-DUP").count() == 1


@pytest.mark.django_db
class TestQuickReceiveView:
    def test_anonymous_redirected_to_login(self, client):
        response = client.get(reverse("inventory:quick_receive"))
        assert response.status_code == 302

    def test_read_only_user_forbidden(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("inventory:quick_receive"))
        assert response.status_code == 403

    def test_creates_a_unit_asset_per_line_and_shows_results(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:quick_receive"),
            {
                "product": unit_product.pk,
                "location": location_tree["room"].pk,
                "occurred_at": date.today().isoformat(),
                "vendor_serials": "SN-QV-1\nSN-QV-2\nSN-QV-3",
                "condition": "new",
            },
        )
        assert response.status_code == 200
        results = response.context["results"]
        assert [r["status"] for r in results] == ["created", "created", "created"]
        assert UnitAsset.objects.filter(vendor_serial__startswith="SN-QV-").count() == 3
        assert "Received 3 of 3" in response.content.decode()

    def test_form_redisplayed_with_product_and_location_preset_for_the_next_batch(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:quick_receive"),
            {
                "product": unit_product.pk,
                "location": location_tree["room"].pk,
                "occurred_at": date.today().isoformat(),
                "vendor_serials": "SN-QV-NEXT-1",
                "condition": "new",
            },
        )
        assert response.context["form"]["product"].value() == unit_product.pk
        assert response.context["form"]["location"].value() == location_tree["room"].pk

    def test_mixed_batch_shows_per_row_outcome(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        from apps.inventory.services.receipts import receive_stock

        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-QV-DUP",
        )

        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:quick_receive"),
            {
                "product": unit_product.pk,
                "location": location_tree["room"].pk,
                "occurred_at": date.today().isoformat(),
                "vendor_serials": "SN-QV-OK\nSN-QV-DUP",
                "condition": "new",
            },
        )
        results = response.context["results"]
        assert [r["status"] for r in results] == ["created", "duplicate"]
        assert "Received 1 of 2" in response.content.decode()

    def test_blank_serials_field_rejected_with_form_error(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:quick_receive"),
            {
                "product": unit_product.pk,
                "location": location_tree["room"].pk,
                "occurred_at": date.today().isoformat(),
                "vendor_serials": "   \n\n",
                "condition": "new",
            },
        )
        assert response.status_code == 200
        assert "results" not in response.context
        assert "Enter at least one serial" in response.content.decode()


@pytest.mark.django_db
class TestUnitAssetListAndDetail:
    def test_list_scoped_to_accessible_locations(
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
            name="X Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM, name="X Room", parent=other_floor, user=administrator
        )

        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-IN-SCOPE",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-OUT-OF-SCOPE",
        )

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:asset_list"))
        serials = {asset.vendor_serial for asset in response.context["assets"]}
        assert "SN-IN-SCOPE" in serials
        assert "SN-OUT-OF-SCOPE" not in serials

    def test_detail_denied_outside_scope(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        other_location_tree,
    ):
        from apps.locations.models import Location
        from apps.locations.services import create_location

        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Y Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM, name="Y Room", parent=other_floor, user=administrator
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-DENY",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DENY")

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:asset_detail", kwargs={"pk": asset.pk}))
        assert response.status_code == 403


@pytest.mark.django_db
class TestStockBalanceListAndDetail:
    def test_list_scoped_to_accessible_locations(
        self, client, stock_manager_with_room_access, quantity_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:balance_list"))
        assert len(response.context["balances"]) == 1

    def test_detail_shows_ledger_lines(
        self, client, stock_manager_with_room_access, quantity_product, location_tree
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )
        balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:balance_detail", kwargs={"pk": balance.pk}))
        assert response.status_code == 200
        assert len(response.context["lines"]) == 1

    def test_sort_by_on_hand_descending(
        self, client, administrator, quantity_product, unit_product, location_tree
    ):
        from apps.catalog.models import TrackingMethod
        from apps.catalog.services import create_product

        other_product = create_product(
            user=administrator,
            brand_name="Zebra",
            model="Z-1",
            product_type_name="Widget",
            tracking_method=TrackingMethod.QUANTITY,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=5,
        )
        receive_stock(
            user=administrator,
            product=other_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=50,
        )

        client.force_login(administrator)
        response = client.get(reverse("inventory:balance_list"), {"sort": "on_hand", "dir": "desc"})
        on_hand_values = [b.on_hand_quantity for b in response.context["balances"]]
        assert on_hand_values == sorted(on_hand_values, reverse=True)
        assert on_hand_values[0] == 50


@pytest.mark.django_db
class TestTransactionDetailView:
    def test_accessible_when_destination_in_scope(
        self, client, stock_manager_with_room_access, unit_product, location_tree
    ):
        txn = receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-TXN-1",
        )

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:transaction_detail", kwargs={"pk": txn.pk}))
        assert response.status_code == 200
        assert len(response.context["lines"]) == 1

    def test_denied_when_destination_out_of_scope(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        other_location_tree,
    ):
        from apps.locations.models import Location
        from apps.locations.services import create_location

        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Z Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM, name="Z Room", parent=other_floor, user=administrator
        )
        txn = receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-TXN-2",
        )

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:transaction_detail", kwargs={"pk": txn.pk}))
        assert response.status_code == 403

    def test_disposal_can_generate_document_but_not_return(
        self, client, administrator, unit_product, location_tree
    ):
        from apps.inventory.services.disposition import dispose

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-TXN-DISPOSAL",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-TXN-DISPOSAL")
        txn = dispose(
            user=administrator,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            notes="eol",
            wipe_method="software_wipe",
        )

        client.force_login(administrator)
        response = client.get(reverse("inventory:transaction_detail", kwargs={"pk": txn.pk}))
        assert response.status_code == 200
        assert response.context["can_generate_document"] is True
        assert response.context["can_return"] is False
        assert b"Generate disposal certificate" in response.content
        assert b"Record a return" not in response.content

    def test_assignment_can_generate_document_and_return(
        self, client, administrator, unit_product, location_tree
    ):
        from apps.inventory.services.assignments import assign_to_employee

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-TXN-ASSIGN",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-TXN-ASSIGN")
        txn = assign_to_employee(
            user=administrator,
            employee_name="Nadia",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        client.force_login(administrator)
        response = client.get(reverse("inventory:transaction_detail", kwargs={"pk": txn.pk}))
        assert response.context["can_generate_document"] is True
        assert response.context["can_return"] is True


@pytest.mark.django_db
class TestUnitAssetListSort:
    """templates/inventory/asset_list.html's clickable column headers —
    apps.inventory.views.UnitAssetListView.SORT_FIELDS.
    """

    @pytest.fixture
    def two_products_in_room(self, administrator, unit_product, location_tree):
        from apps.catalog.models import TrackingMethod
        from apps.catalog.services import create_product

        other_product = create_product(
            user=administrator,
            brand_name="Aruba",
            model="AP-100",
            product_type_name="Access Point",
            tracking_method=TrackingMethod.UNIT,
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-SORT-FORTINET",
        )
        receive_stock(
            user=administrator,
            product=other_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-SORT-ARUBA",
        )
        return {"fortinet": unit_product, "aruba": other_product}

    def test_default_order_is_unaffected(self, client, administrator, two_products_in_room):
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_list"))
        assert response.status_code == 200
        # Most-recently-created first — Aruba was received second.
        serials = [asset.vendor_serial for asset in response.context["assets"]]
        assert serials.index("SN-SORT-ARUBA") < serials.index("SN-SORT-FORTINET")

    def test_sort_by_product_ascending(self, client, administrator, two_products_in_room):
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_list"), {"sort": "product", "dir": "asc"})
        serials = [asset.vendor_serial for asset in response.context["assets"]]
        assert serials.index("SN-SORT-ARUBA") < serials.index("SN-SORT-FORTINET")

    def test_sort_by_product_descending(self, client, administrator, two_products_in_room):
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_list"), {"sort": "product", "dir": "desc"})
        serials = [asset.vendor_serial for asset in response.context["assets"]]
        assert serials.index("SN-SORT-FORTINET") < serials.index("SN-SORT-ARUBA")

    def test_unknown_sort_key_falls_back_to_default(
        self, client, administrator, two_products_in_room
    ):
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_list"), {"sort": "not-a-real-field"})
        assert response.status_code == 200

    def test_sort_link_preserves_active_filters(self, client, administrator, two_products_in_room):
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_list"), {"status": "in_stock"})
        assert response.status_code == 200
        content = response.content.decode()
        assert "status=in_stock" in content
        assert "sort=product" in content
