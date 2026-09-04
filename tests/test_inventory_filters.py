from datetime import date

import pytest
from django.urls import reverse

from apps.inventory.services.receipts import receive_stock


@pytest.fixture
def rack(administrator, location_tree):
    from apps.locations.models import Location
    from apps.locations.services import create_location

    return create_location(
        level=Location.Level.RACK_CABINET,
        name="Filter Rack",
        parent=location_tree["room"],
        user=administrator,
    )


@pytest.mark.django_db
class TestAssetListFilters:
    def test_brand_filter(self, client, administrator, location_tree):
        from apps.catalog.models import TrackingMethod
        from apps.catalog.services import create_product

        fortinet = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="F1",
            product_type_name="Firewall",
            tracking_method=TrackingMethod.UNIT,
        )
        cisco = create_product(
            user=administrator,
            brand_name="Cisco",
            model="C1",
            product_type_name="Switch",
            tracking_method=TrackingMethod.UNIT,
        )
        receive_stock(
            user=administrator,
            product=fortinet,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-F1",
        )
        receive_stock(
            user=administrator,
            product=cisco,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-C1",
        )

        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_list"), {"brand": "Fortinet"})
        serials = {a.vendor_serial for a in response.context["assets"]}
        assert serials == {"SN-F1"}

    def test_status_filter(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-STATUS-1",
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_list"), {"status": "damaged"})
        assert list(response.context["assets"]) == []

    def test_location_hierarchy_filter_includes_descendants(
        self, client, administrator, unit_product, location_tree, rack
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=rack,
            occurred_at=date.today(),
            vendor_serial="SN-LOC-1",
        )

        client.force_login(administrator)
        response = client.get(
            reverse("inventory:asset_list"), {"location": str(location_tree["room"].pk)}
        )
        serials = {a.vendor_serial for a in response.context["assets"]}
        assert "SN-LOC-1" in serials  # rack is a descendant of room

    def test_project_reference_filter(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PR-1",
            project_reference="PRJ-FILTER",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PR-2",
            project_reference="OTHER",
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_list"), {"project_reference": "PRJ-FILTER"})
        serials = {a.vendor_serial for a in response.context["assets"]}
        assert serials == {"SN-PR-1"}

    def test_arrival_date_range_filter(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date(2020, 1, 1),
            vendor_serial="SN-OLD",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-NEW",
        )

        client.force_login(administrator)
        response = client.get(
            reverse("inventory:asset_list"),
            {"arrival_after": "2020-01-01", "arrival_before": "2020-12-31"},
        )
        serials = {a.vendor_serial for a in response.context["assets"]}
        assert serials == {"SN-OLD"}

    def test_duplicate_serial_filter(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DUPFILTER",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DUPFILTER",
            duplicate_serial_acknowledged=True,
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-UNIQUE",
        )

        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_list"), {"duplicate_serial": "1"})
        serials = {a.vendor_serial for a in response.context["assets"]}
        assert serials == {"SN-DUPFILTER"}

    def test_filters_are_scoped_first(
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
            name="F",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM, name="R", parent=other_floor, user=administrator
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-OUTSIDE",
        )
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-INSIDE",
        )

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:asset_list"), {"q": "SN"})
        serials = {a.vendor_serial for a in response.context["assets"]}
        assert serials == {"SN-INSIDE"}


@pytest.mark.django_db
class TestBalanceListFilters:
    def test_type_filter(self, client, administrator, quantity_product, location_tree):
        client.force_login(administrator)
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=5,
        )
        response = client.get(
            reverse("inventory:balance_list"), {"type": quantity_product.product_type.name}
        )
        assert len(response.context["balances"]) == 1

    def test_location_filter(self, client, administrator, quantity_product, location_tree, rack):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=rack,
            occurred_at=date.today(),
            quantity=3,
        )
        client.force_login(administrator)
        response = client.get(
            reverse("inventory:balance_list"), {"location": str(location_tree["room"].pk)}
        )
        assert len(response.context["balances"]) == 1

    def test_csv_export_content(self, client, administrator, quantity_product, location_tree):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=12,
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:balance_list"), {"format": "csv"})

        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        body = response.content.decode()
        lines = body.strip().splitlines()
        assert lines[0] == "Brand,Model,SKU,Type,Location,Stock Purpose,On Hand,Reserved,Available"
        assert any(
            quantity_product.model in line and ",Internal,12,0,12" in line for line in lines[1:]
        )
