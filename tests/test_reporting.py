from datetime import date

import pytest
from django.urls import reverse

from apps.catalog.services import update_product
from apps.inventory.models import UnitAsset
from apps.inventory.services.assignments import assign_to_employee, deliver_to_customer
from apps.inventory.services.disposition import dispose, mark_damaged, mark_lost
from apps.inventory.services.receipts import receive_stock
from apps.inventory.services.reservations import reserve_stock


@pytest.fixture
def other_room(administrator, other_location_tree):
    from apps.locations.models import Location
    from apps.locations.services import create_location

    other_floor = create_location(
        level=Location.Level.FLOOR,
        name="Rep Floor",
        parent=other_location_tree["site"],
        user=administrator,
    )
    return create_location(
        level=Location.Level.STORAGE_ROOM, name="Rep Room", parent=other_floor, user=administrator
    )


@pytest.mark.django_db
class TestReportsHubAndAccess:
    def test_anonymous_redirected(self, client):
        response = client.get(reverse("reporting:hub"))
        assert response.status_code == 302

    def test_read_only_user_can_view_reports(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("reporting:hub"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestDamagedLostDisposedReports:
    def test_damaged_assets_report_scoped_and_filtered(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        location_tree,
        other_room,
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DMG-IN",
        )
        asset_in = UnitAsset.objects.get(vendor_serial="SN-DMG-IN")
        mark_damaged(
            user=stock_manager_with_room_access,
            occurred_at=date.today(),
            unit_asset_ids=[asset_in.pk],
            notes="x",
        )

        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-DMG-OUT",
        )
        asset_out = UnitAsset.objects.get(vendor_serial="SN-DMG-OUT")
        mark_damaged(
            user=administrator, occurred_at=date.today(), unit_asset_ids=[asset_out.pk], notes="x"
        )

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("reporting:damaged_assets"))
        serials = {a.vendor_serial for a in response.context["assets"]}
        assert serials == {"SN-DMG-IN"}

    def test_lost_assets_report(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-LOST-R",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-LOST-R")
        mark_lost(
            user=administrator, occurred_at=date.today(), unit_asset_ids=[asset.pk], notes="missing"
        )

        client.force_login(administrator)
        response = client.get(reverse("reporting:lost_assets"))
        serials = {a.vendor_serial for a in response.context["assets"]}
        assert serials == {"SN-LOST-R"}

    def test_disposed_items_report_includes_hdd_and_survives_after_disposal(
        self, client, administrator, location_tree
    ):
        from apps.catalog.models import TrackingMethod
        from apps.catalog.services import create_product

        hdd = create_product(
            user=administrator,
            brand_name="Seagate",
            model="ST2000",
            product_type_name="HDD",
            tracking_method=TrackingMethod.UNIT,
        )
        receive_stock(
            user=administrator,
            product=hdd,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="HDD-REPORT-1",
        )
        asset = UnitAsset.objects.get(vendor_serial="HDD-REPORT-1")
        dispose(
            user=administrator,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            notes="sanitized",
        )

        client.force_login(administrator)
        response = client.get(reverse("reporting:disposed_items"))
        serials = {a.vendor_serial for a in response.context["assets"]}
        assert "HDD-REPORT-1" in serials

    def test_disposed_items_type_filter(self, client, administrator, location_tree):
        from apps.catalog.models import TrackingMethod
        from apps.catalog.services import create_product

        hdd = create_product(
            user=administrator,
            brand_name="WD",
            model="WD1",
            product_type_name="HDD",
            tracking_method=TrackingMethod.UNIT,
        )
        keyboard = create_product(
            user=administrator,
            brand_name="Logitech",
            model="K1",
            product_type_name="Keyboard",
            tracking_method=TrackingMethod.UNIT,
        )
        receive_stock(
            user=administrator,
            product=hdd,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="HDD-2",
        )
        receive_stock(
            user=administrator,
            product=keyboard,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="KB-2",
        )
        dispose(
            user=administrator,
            occurred_at=date.today(),
            unit_asset_ids=[UnitAsset.objects.get(vendor_serial="HDD-2").pk],
            notes="x",
        )
        dispose(
            user=administrator,
            occurred_at=date.today(),
            unit_asset_ids=[UnitAsset.objects.get(vendor_serial="KB-2").pk],
            notes="x",
        )

        client.force_login(administrator)
        response = client.get(reverse("reporting:disposed_items"), {"type": "HDD"})
        serials = {a.vendor_serial for a in response.context["assets"]}
        assert serials == {"HDD-2"}


@pytest.mark.django_db
class TestAssignmentDeliveryReports:
    def test_employee_assignments_report(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-EMP-1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-EMP-1")
        assign_to_employee(
            user=administrator,
            employee_name="Rita",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        client.force_login(administrator)
        response = client.get(reverse("reporting:employee_assignments"))
        employees = {t.employee_name for t in response.context["transactions"]}
        assert "Rita" in employees

    def test_customer_deliveries_report(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DEL-1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DEL-1")
        deliver_to_customer(
            user=administrator,
            final_customer="Acme Reports",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        client.force_login(administrator)
        response = client.get(reverse("reporting:customer_deliveries"))
        customers = {t.final_customer for t in response.context["transactions"]}
        assert "Acme Reports" in customers

    def test_temporary_assignments_report_shows_only_temporary(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-TEMP-1",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PERM-1",
        )
        asset_temp = UnitAsset.objects.get(vendor_serial="SN-TEMP-1")
        asset_perm = UnitAsset.objects.get(vendor_serial="SN-PERM-1")
        assign_to_employee(
            user=administrator,
            employee_name="Temp Person",
            occurred_at=date.today(),
            unit_asset_ids=[asset_temp.pk],
            is_temporary_assignment=True,
        )
        assign_to_employee(
            user=administrator,
            employee_name="Perm Person",
            occurred_at=date.today(),
            unit_asset_ids=[asset_perm.pk],
            is_temporary_assignment=False,
        )

        client.force_login(administrator)
        response = client.get(reverse("reporting:temporary_assignments"))
        employees = {t.employee_name for t in response.context["transactions"]}
        assert employees == {"Temp Person"}


@pytest.mark.django_db
class TestStockByProjectReferenceReport:
    def test_search_returns_matching_units_and_reservations(
        self, client, administrator, unit_product, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PROJ-1",
            project_reference="PRJ-REPORT",
        )
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
            project_reference="PRJ-REPORT",
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 3}
            ],
        )

        client.force_login(administrator)
        response = client.get(
            reverse("reporting:stock_by_project_reference"), {"project_reference": "PRJ-REPORT"}
        )
        serials = {a.vendor_serial for a in response.context["units"]}
        assert serials == {"SN-PROJ-1"}
        assert response.context["reservations"].count() == 1

    def test_no_query_lists_distinct_references(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-PROJ-2",
            project_reference="PRJ-DISTINCT",
        )
        client.force_login(administrator)
        response = client.get(reverse("reporting:stock_by_project_reference"))
        assert "PRJ-DISTINCT" in list(response.context["distinct_references"])


@pytest.mark.django_db
class TestMovementHistoryReport:
    def test_shows_history_scoped_to_user(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        location_tree,
        other_room,
    ):
        receive_stock(
            user=stock_manager_with_room_access,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-HIST-IN",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-HIST-OUT",
        )

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("reporting:movement_history"))
        serials = {e.unit_asset.vendor_serial for e in response.context["events"]}
        assert "SN-HIST-IN" in serials
        assert "SN-HIST-OUT" not in serials

    def test_filter_by_specific_asset(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-HIST-A",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-HIST-B",
        )
        asset_a = UnitAsset.objects.get(vendor_serial="SN-HIST-A")

        client.force_login(administrator)
        response = client.get(reverse("reporting:movement_history"), {"asset": str(asset_a.pk)})
        serials = {e.unit_asset.vendor_serial for e in response.context["events"]}
        assert serials == {"SN-HIST-A"}


@pytest.mark.django_db
class TestLowStockReport:
    def test_empty_when_no_threshold_configured(self, client, administrator, location_tree):
        from apps.catalog.models import TrackingMethod
        from apps.catalog.services import create_product

        no_threshold_product = create_product(
            user=administrator,
            brand_name="Generic",
            model="No Threshold Product",
            product_type_name="Accessory",
            tracking_method=TrackingMethod.QUANTITY,
        )
        receive_stock(
            user=administrator,
            product=no_threshold_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=1,
        )

        client.force_login(administrator)
        response = client.get(reverse("reporting:low_stock"))
        assert no_threshold_product not in [b.product for b in response.context["balances"]]

    def test_shows_product_below_threshold(
        self, client, administrator, quantity_product, location_tree
    ):
        update_product(
            product=quantity_product,
            user=administrator,
            brand_name=quantity_product.brand.name,
            model=quantity_product.model,
            product_type_name=quantity_product.product_type.name,
            tracking_method=quantity_product.tracking_method,
            low_stock_threshold=10,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )

        client.force_login(administrator)
        response = client.get(reverse("reporting:low_stock"))
        assert len(response.context["balances"]) == 1

    def test_does_not_show_product_above_threshold(
        self, client, administrator, quantity_product, location_tree
    ):
        update_product(
            product=quantity_product,
            user=administrator,
            brand_name=quantity_product.brand.name,
            model=quantity_product.model,
            product_type_name=quantity_product.product_type.name,
            tracking_method=quantity_product.tracking_method,
            low_stock_threshold=2,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=50,
        )

        client.force_login(administrator)
        response = client.get(reverse("reporting:low_stock"))
        assert list(response.context["balances"]) == []


@pytest.mark.django_db
class TestStockByLocationReport:
    def test_aggregates_units_and_quantity_per_location(
        self, client, administrator, unit_product, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-AGG-1",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-AGG-2",
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=7,
        )

        client.force_login(administrator)
        response = client.get(reverse("reporting:stock_by_location"))
        rows = {row["location"].pk: row for row in response.context["rows"]}
        row = rows[location_tree["room"].pk]
        assert row["unit_count"] == 2
        assert row["quantity_total"] == 7


@pytest.mark.django_db
class TestCurrentStockReport:
    """Regression coverage for the pagination fix found by measuring real
    wall-clock timing against the 8,000+-row bulk-seeded dataset during
    Prompt 8: this view originally rendered every in-stock unit in one
    unpaginated response (~1.2s against 8,000 rows), violating spec §21.15.
    """

    def test_shows_in_stock_units_and_balances(
        self, client, administrator, unit_product, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-CUR-1",
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=4,
        )

        client.force_login(administrator)
        response = client.get(reverse("reporting:current_stock"))
        serials = {a.vendor_serial for a in response.context["units"]}
        assert serials == {"SN-CUR-1"}
        assert response.context["balances"].count() == 1

    def test_units_are_paginated(self, client, administrator, unit_product, location_tree):
        for i in range(55):
            receive_stock(
                user=administrator,
                product=unit_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                vendor_serial=f"SN-CUR-PAGE-{i:03d}",
            )

        client.force_login(administrator)
        response = client.get(reverse("reporting:current_stock"))
        assert response.context["is_paginated"] is True
        assert len(response.context["units"]) == 50

        page2 = client.get(reverse("reporting:current_stock"), {"page": 2})
        assert len(page2.context["units"]) == 5

    def test_excludes_out_of_scope_units(
        self,
        client,
        administrator,
        stock_manager_with_room_access,
        unit_product,
        location_tree,
        other_room,
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-OUT-OF-SCOPE",
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("reporting:current_stock"))
        serials = {a.vendor_serial for a in response.context["units"]}
        assert "SN-OUT-OF-SCOPE" not in serials


@pytest.mark.django_db
class TestReservedStockReport:
    def test_units_are_paginated(self, client, administrator, unit_product, location_tree):
        assets = []
        for i in range(55):
            receive_stock(
                user=administrator,
                product=unit_product,
                location=location_tree["room"],
                occurred_at=date.today(),
                vendor_serial=f"SN-RES-PAGE-{i:03d}",
            )
            assets.append(UnitAsset.objects.get(vendor_serial=f"SN-RES-PAGE-{i:03d}"))

        reserve_stock(
            user=administrator,
            occurred_at=date.today(),
            project_reference="PRJ-RESERVE-PAGE",
            unit_asset_ids=[a.pk for a in assets],
        )

        client.force_login(administrator)
        response = client.get(reverse("reporting:reserved_stock"))
        assert response.context["is_paginated"] is True
        assert len(response.context["units"]) == 50
