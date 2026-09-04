from datetime import date

import pytest
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse

from apps.core.models import RecentlyViewed
from apps.core.recently_viewed import recently_viewed_for, record_recently_viewed
from apps.inventory.models import InventoryTransaction, UnitAsset
from apps.inventory.services.receipts import receive_stock


@pytest.mark.django_db
class TestRecordRecentlyViewed:
    def test_creates_a_row_on_first_view(self, administrator, unit_product):
        record_recently_viewed(user=administrator, obj=unit_product)
        assert RecentlyViewed.objects.filter(user=administrator).count() == 1

    def test_repeat_view_updates_in_place_not_duplicated(self, administrator, unit_product):
        record_recently_viewed(user=administrator, obj=unit_product)
        first = RecentlyViewed.objects.get(user=administrator)
        first_viewed_at = first.viewed_at

        record_recently_viewed(user=administrator, obj=unit_product)
        assert RecentlyViewed.objects.filter(user=administrator).count() == 1
        first.refresh_from_db()
        assert first.viewed_at >= first_viewed_at

    def test_different_objects_create_separate_rows(
        self, administrator, unit_product, quantity_product
    ):
        record_recently_viewed(user=administrator, obj=unit_product)
        record_recently_viewed(user=administrator, obj=quantity_product)
        assert RecentlyViewed.objects.filter(user=administrator).count() == 2

    def test_anonymous_user_is_a_no_op(self, unit_product):
        record_recently_viewed(user=AnonymousUser(), obj=unit_product)
        assert RecentlyViewed.objects.count() == 0


@pytest.mark.django_db
class TestRecentlyViewedFor:
    def test_orders_most_recent_first(self, administrator, unit_product, quantity_product):
        record_recently_viewed(user=administrator, obj=unit_product)
        record_recently_viewed(user=administrator, obj=quantity_product)
        entries = recently_viewed_for(administrator)
        assert [e["object"] for e in entries] == [quantity_product, unit_product]

    def test_type_labels(self, administrator, unit_product, quantity_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-RECENT-1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-RECENT-1")
        txn = InventoryTransaction.objects.first()

        record_recently_viewed(user=administrator, obj=unit_product)
        record_recently_viewed(user=administrator, obj=asset)
        record_recently_viewed(user=administrator, obj=txn)

        labels = {e["type_label"] for e in recently_viewed_for(administrator)}
        assert labels == {"Product", "Asset", "Transaction"}

    def test_only_shows_this_users_own_history(self, administrator, stock_manager, unit_product):
        record_recently_viewed(user=administrator, obj=unit_product)
        assert recently_viewed_for(stock_manager) == []

    def test_respects_limit(self, administrator, unit_product, quantity_product):
        record_recently_viewed(user=administrator, obj=unit_product)
        record_recently_viewed(user=administrator, obj=quantity_product)
        assert len(recently_viewed_for(administrator, limit=1)) == 1


@pytest.mark.django_db
class TestRecentlyViewedViewHooks:
    """The three detail views' get() overrides that call
    record_recently_viewed()."""

    def test_product_detail_view_records_a_view(self, client, administrator, unit_product):
        client.force_login(administrator)
        client.get(reverse("catalog:product_detail", kwargs={"pk": unit_product.pk}))
        assert RecentlyViewed.objects.filter(user=administrator).count() == 1

    def test_product_detail_ajax_request_does_not_record(self, client, administrator, unit_product):
        client.force_login(administrator)
        client.get(
            reverse("catalog:product_detail", kwargs={"pk": unit_product.pk}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert RecentlyViewed.objects.filter(user=administrator).count() == 0

    def test_asset_detail_view_records_a_view(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-RECENT-HOOK",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-RECENT-HOOK")
        client.force_login(administrator)
        client.get(reverse("inventory:asset_detail", kwargs={"pk": asset.pk}))
        assert RecentlyViewed.objects.filter(user=administrator).count() == 1

    def test_asset_detail_ajax_request_does_not_record(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-RECENT-HOOK-AJAX",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-RECENT-HOOK-AJAX")
        client.force_login(administrator)
        client.get(
            reverse("inventory:asset_detail", kwargs={"pk": asset.pk}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert RecentlyViewed.objects.filter(user=administrator).count() == 0

    def test_transaction_detail_view_records_a_view(
        self, client, administrator, unit_product, location_tree
    ):
        txn = receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-RECENT-TXN",
        )
        client.force_login(administrator)
        client.get(reverse("inventory:transaction_detail", kwargs={"pk": txn.pk}))
        assert RecentlyViewed.objects.filter(user=administrator).count() == 1

    def test_home_view_lists_recently_viewed_entries(self, client, administrator, unit_product):
        client.force_login(administrator)
        client.get(reverse("catalog:product_detail", kwargs={"pk": unit_product.pk}))
        response = client.get(reverse("core:home"))
        objects = [e["object"] for e in response.context["recently_viewed"]]
        assert unit_product in objects
