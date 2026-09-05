"""Acceptance criterion §21.15: lists stay paginated and responsive with at
least 8,000 records. Query-count assertions are the reliable regression
signal here (a stray N+1 shows up immediately regardless of machine speed);
wall-clock timing is not asserted since it's inherently environment-dependent.
"""

from datetime import date

import pytest
from django.urls import reverse

from apps.catalog.models import Brand, ItemCategory, Product, ProductType, TrackingMethod
from apps.inventory.models import UnitAsset, UnitStatus


@pytest.fixture
def bulk_assets(administrator, location_tree):
    brand = Brand.objects.create(name="Perf Brand")
    product_type = ProductType.objects.create(name="Perf Type")
    product = Product.objects.create(
        brand=brand,
        model="Perf Model",
        normalized_model="perf model",
        product_type=product_type,
        tracking_method=TrackingMethod.UNIT,
        category=ItemCategory.SERIALIZED_ASSET,
        created_by=administrator,
        updated_by=administrator,
    )

    batch = []
    total = 8200
    for i in range(total):
        serial = f"PERF-{i:08d}"
        batch.append(
            UnitAsset(
                product=product,
                vendor_serial=serial,
                normalized_serial=serial,
                status=UnitStatus.IN_STOCK,
                current_location=location_tree["room"],
                arrival_date=date.today(),
                created_by=administrator,
                updated_by=administrator,
            )
        )
        if len(batch) >= 1000:
            UnitAsset.objects.bulk_create(batch)
            batch = []
    if batch:
        UnitAsset.objects.bulk_create(batch)

    return {"product": product, "total": total}


@pytest.mark.django_db
class TestAssetListPerformance:
    def test_list_is_paginated_not_loading_everything(self, client, administrator, bulk_assets):
        client.force_login(administrator)
        response = client.get(reverse("inventory:asset_list"))

        assert response.status_code == 200
        assert len(response.context["assets"]) == 50  # one page, not all 8,200
        assert response.context["page_obj"].paginator.count == bulk_assets["total"]

    def test_list_query_count_does_not_scale_with_row_count(
        self, client, administrator, bulk_assets, django_assert_max_num_queries
    ):
        client.force_login(administrator)
        # A handful of fixed queries (auth, scope check, count, page of
        # results with its select_related joins, plus one flat SystemSettings
        # lookup shared by apps.settings.middleware/context_processors for
        # branding + the ALLOWED_HOSTS override) — not one per row. This is
        # the real regression guard: an N+1 here would blow well past 11
        # regardless of how fast the box is.
        with django_assert_max_num_queries(11):
            response = client.get(reverse("inventory:asset_list"))
        assert response.status_code == 200

    def test_filtered_list_still_bounded_query_count(
        self, client, administrator, bulk_assets, django_assert_max_num_queries
    ):
        client.force_login(administrator)
        with django_assert_max_num_queries(11):
            response = client.get(
                reverse("inventory:asset_list"), {"status": "in_stock", "brand": "Perf"}
            )
        assert response.status_code == 200

    def test_csv_export_returns_all_matching_rows_not_one_page(
        self, client, administrator, bulk_assets
    ):
        client.force_login(administrator)
        response = client.get(
            reverse("inventory:asset_list"), {"format": "csv", "brand": "Perf Brand"}
        )
        assert response.status_code == 200
        # header row + one row per bulk asset
        body = response.content.decode()
        assert body.count("\n") >= bulk_assets["total"]


@pytest.mark.django_db
class TestSeedBulkInventoryCommand:
    def test_creates_requested_count(self, administrator, location_tree):
        from django.core.management import call_command
        from django.test import override_settings

        with override_settings(DEBUG=True):
            call_command("seed_bulk_inventory", count=25)

        assert UnitAsset.objects.filter(vendor_serial__startswith="BULK-").count() == 25

    def test_rerunning_with_the_same_count_does_not_duplicate(self, administrator, location_tree):
        from django.core.management import call_command
        from django.test import override_settings

        with override_settings(DEBUG=True):
            call_command("seed_bulk_inventory", count=10)
            call_command("seed_bulk_inventory", count=10)

        assert UnitAsset.objects.filter(vendor_serial__startswith="BULK-").count() == 10

    def test_rerunning_with_a_higher_count_tops_up_rather_than_duplicating(
        self, administrator, location_tree
    ):
        from django.core.management import call_command
        from django.test import override_settings

        with override_settings(DEBUG=True):
            call_command("seed_bulk_inventory", count=10)
            call_command("seed_bulk_inventory", count=15)

        assert UnitAsset.objects.filter(vendor_serial__startswith="BULK-").count() == 15

    def test_refuses_to_run_outside_debug(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from django.test import override_settings

        with override_settings(DEBUG=False):
            with pytest.raises(CommandError):
                call_command("seed_bulk_inventory", count=5)
