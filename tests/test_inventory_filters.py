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
        from apps.catalog.models import ItemCategory
        from apps.catalog.services import create_product

        fortinet = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="F1",
            product_type_name="Firewall",
            category=ItemCategory.SERIALIZED_ASSET,
        )
        cisco = create_product(
            user=administrator,
            brand_name="Cisco",
            model="C1",
            product_type_name="Switch",
            category=ItemCategory.SERIALIZED_ASSET,
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
class TestAssetListBulkExport:
    """apps.core.csv_export.CSVExportMixin's ?ids= path — the Assets grid's
    "Export selected (N)" bulk action (static/js/inventory_grid.js's
    cross-page selectedById), distinct from "Export filtered CSV" which
    exports whatever the grid's own filters currently match.
    """

    def test_exports_only_the_requested_ids(self, client, administrator, location_tree):
        from apps.catalog.models import ItemCategory
        from apps.catalog.services import create_product

        fortinet = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="F1",
            product_type_name="Firewall",
            category=ItemCategory.SERIALIZED_ASSET,
        )
        cisco = create_product(
            user=administrator,
            brand_name="Cisco",
            model="C1",
            product_type_name="Switch",
            category=ItemCategory.SERIALIZED_ASSET,
        )
        receive_stock(
            user=administrator,
            product=fortinet,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-BULK-KEEP",
        )
        receive_stock(
            user=administrator,
            product=cisco,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-BULK-DROP",
        )
        from apps.inventory.models import UnitAsset

        keep = UnitAsset.objects.get(vendor_serial="SN-BULK-KEEP")

        client.force_login(administrator)
        response = client.get(
            reverse("inventory:asset_list"), {"format": "csv", "ids": str(keep.pk)}
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert "SN-BULK-KEEP" in body
        assert "SN-BULK-DROP" not in body

    def test_without_ids_exports_everything_in_scope(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-BULK-ALL",
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_list"), {"format": "csv"})
        assert "SN-BULK-ALL" in response.content.decode()

    def test_ids_outside_the_users_scope_are_not_exported(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        other_location_tree,
    ):
        from apps.inventory.models import UnitAsset
        from apps.locations.models import Location
        from apps.locations.services import create_location

        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Bulk Export Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Bulk Export Room",
            parent=other_floor,
            user=administrator,
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-OUT-OF-SCOPE-EXPORT",
        )
        out_of_scope = UnitAsset.objects.get(vendor_serial="SN-OUT-OF-SCOPE-EXPORT")

        client.force_login(stock_manager_with_room_access)
        response = client.get(
            reverse("inventory:asset_list"), {"format": "csv", "ids": str(out_of_scope.pk)}
        )
        body = response.content.decode()
        assert "SN-OUT-OF-SCOPE-EXPORT" not in body


@pytest.mark.django_db
class TestAssetListExportCurrentView:
    """apps.core.csv_export.CSVExportMixin's dynamic ?columns= path — the
    Assets grid's "Export current view" link sends the grid's own current
    column layout (order/title/visibility) so the CSV matches what's on
    screen, distinct from the fixed-column "Export filtered CSV".
    """

    def test_exports_only_the_requested_columns_in_order(
        self, client, administrator, unit_product, location_tree
    ):
        import json

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-VIEW-COLS",
        )
        columns = json.dumps(
            [
                {"field": "serial", "title": "Serial #", "visible": True},
                {"field": "brand", "title": "Brand", "visible": True},
                {"field": "id", "title": "Internal ID", "visible": True},
                {"field": "sku", "title": "SKU", "visible": False},
            ]
        )
        client.force_login(administrator)
        response = client.get(
            reverse("inventory:asset_list"), {"format": "csv", "columns": columns}
        )
        body = response.content.decode()
        lines = body.strip().splitlines()
        # "id" dropped (structural, not a real column) and "sku" dropped
        # (visible: False) — only serial and brand remain, in that order.
        assert lines[0] == "Serial #,Brand"
        assert any(line.startswith("SN-VIEW-COLS,") for line in lines[1:])

    def test_without_columns_falls_back_to_the_fixed_export(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-VIEW-DEFAULT",
        )
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_list"), {"format": "csv"})
        body = response.content.decode()
        assert body.strip().splitlines()[0] == (
            "Brand,Model,SKU,Type,Serial,Status,Stock Purpose,Location,"
            "Assigned To,Project Reference,Final Customer,Arrival Date,Removal Date"
        )


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
