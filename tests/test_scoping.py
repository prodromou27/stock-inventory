from datetime import date

import pytest
from django.core.exceptions import PermissionDenied

from apps.accounts.models import UserLocationAccess
from apps.accounts.services import grant_location_access, revoke_location_access
from apps.locations.models import Location
from apps.locations.scoping import accessible_locations, require_location_access, scope_queryset
from apps.locations.services import create_location


@pytest.mark.django_db
class TestAccessibleLocations:
    def test_administrator_sees_every_location(
        self, administrator, location_tree, other_location_tree
    ):
        names = set(accessible_locations(administrator).values_list("name", flat=True))
        assert location_tree["country"].name in names
        assert other_location_tree["country"].name in names

    def test_user_with_no_grants_sees_nothing(self, read_only_user, location_tree):
        assert accessible_locations(read_only_user).count() == 0

    def test_grant_cascades_to_descendants_only(
        self, administrator, read_only_user, location_tree, other_location_tree
    ):
        grant_location_access(
            user=read_only_user, location=location_tree["floor"], granted_by=administrator
        )

        names = set(accessible_locations(read_only_user).values_list("name", flat=True))
        assert names == {location_tree["floor"].name, location_tree["room"].name}
        assert location_tree["site"].name not in names  # ancestor, not granted
        assert location_tree["country"].name not in names  # ancestor, not granted
        assert other_location_tree["country"].name not in names  # unrelated tree

    def test_multiple_grants_are_unioned(
        self, administrator, read_only_user, location_tree, other_location_tree
    ):
        grant_location_access(
            user=read_only_user, location=location_tree["room"], granted_by=administrator
        )
        grant_location_access(
            user=read_only_user, location=other_location_tree["site"], granted_by=administrator
        )

        names = set(accessible_locations(read_only_user).values_list("name", flat=True))
        assert names == {location_tree["room"].name, other_location_tree["site"].name}

    def test_country_grant_automatically_covers_a_location_created_afterward(
        self, administrator, stock_manager, location_tree
    ):
        """Country access must cover every FUTURE child too, not just what
        existed at grant time — proven end-to-end (grant, then create, then
        check visibility), not just at the ltree-lookup unit-test level.
        """
        grant_location_access(
            user=stock_manager, location=location_tree["country"], granted_by=administrator
        )
        new_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Freshly Created Room",
            parent=location_tree["floor"],
            user=administrator,
        )

        names = set(accessible_locations(stock_manager).values_list("name", flat=True))
        assert new_room.name in names
        # Sanity: every existing level is covered too, not just the new one.
        assert location_tree["site"].name in names
        assert location_tree["floor"].name in names
        assert location_tree["room"].name in names

    def test_revoking_country_access_immediately_blocks_all_descendants(
        self, administrator, stock_manager, location_tree, other_location_tree
    ):
        """Revocation must take effect on the very next request — proven by
        re-checking accessible_locations() after revoke, not just that the
        UserLocationAccess row is gone (scope_queryset recomputes from grants
        fresh on every call, with nothing cached, so this should already hold
        — this test locks that guarantee in against future regressions, e.g.
        an accidental caching layer added later).
        """
        grant_location_access(
            user=stock_manager, location=location_tree["country"], granted_by=administrator
        )
        assert location_tree["room"].name in set(
            accessible_locations(stock_manager).values_list("name", flat=True)
        )

        access = UserLocationAccess.objects.get(
            user=stock_manager, location=location_tree["country"]
        )
        revoke_location_access(access=access, revoked_by=administrator)

        assert accessible_locations(stock_manager).count() == 0
        with pytest.raises(PermissionDenied):
            require_location_access(stock_manager, location_tree["room"])
        # History is preserved — the Location rows themselves still exist.
        assert Location.objects.filter(pk=location_tree["room"].pk).exists()

    def test_revoking_country_access_blocks_real_inventory_data_immediately(
        self, administrator, stock_manager, location_tree
    ):
        """Not just Location rows — a UnitAsset in a now-unauthorized room
        must also disappear from a scoped queryset the instant access is
        revoked.
        """
        from apps.inventory.models import UnitAsset
        from apps.inventory.services.receipts import receive_stock
        from apps.locations.scoping import scope_queryset

        product = _make_unit_product(administrator)
        receive_stock(
            user=administrator,
            product=product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-SCOPE-REVOKE",
        )

        grant_location_access(
            user=stock_manager, location=location_tree["country"], granted_by=administrator
        )
        assert (
            scope_queryset(
                stock_manager, UnitAsset.objects.all(), location_field="current_location"
            ).count()
            == 1
        )

        access = UserLocationAccess.objects.get(
            user=stock_manager, location=location_tree["country"]
        )
        revoke_location_access(access=access, revoked_by=administrator)

        assert (
            scope_queryset(
                stock_manager, UnitAsset.objects.all(), location_field="current_location"
            ).count()
            == 0
        )


def _make_unit_product(administrator):
    from apps.catalog.models import ItemCategory
    from apps.catalog.services import create_product

    return create_product(
        user=administrator,
        brand_name="ScopeTest",
        model="Widget",
        product_type_name="Gadget",
        category=ItemCategory.SERIALIZED_ASSET,
    )


@pytest.mark.django_db
class TestScopeQueryset:
    def test_scopes_the_location_queryset_itself(
        self, administrator, read_only_user, location_tree
    ):
        grant_location_access(
            user=read_only_user, location=location_tree["room"], granted_by=administrator
        )

        scoped = scope_queryset(read_only_user, Location.objects.all(), location_field=None)
        assert list(scoped) == [location_tree["room"]]


@pytest.mark.django_db
class TestRequireLocationAccess:
    def test_administrator_always_allowed(self, administrator, other_location_tree):
        require_location_access(administrator, other_location_tree["country"])  # must not raise

    def test_none_location_always_allowed(self, read_only_user):
        require_location_access(read_only_user, None)  # must not raise

    def test_granted_location_allowed(self, administrator, read_only_user, location_tree):
        grant_location_access(
            user=read_only_user, location=location_tree["floor"], granted_by=administrator
        )
        require_location_access(read_only_user, location_tree["floor"])  # must not raise
        require_location_access(read_only_user, location_tree["room"])  # descendant, must not raise

    def test_out_of_scope_location_denied(
        self, administrator, read_only_user, location_tree, other_location_tree
    ):
        grant_location_access(
            user=read_only_user, location=location_tree["floor"], granted_by=administrator
        )
        with pytest.raises(PermissionDenied):
            require_location_access(read_only_user, other_location_tree["country"])

    def test_ancestor_of_granted_location_denied(
        self, administrator, read_only_user, location_tree
    ):
        grant_location_access(
            user=read_only_user, location=location_tree["floor"], granted_by=administrator
        )
        with pytest.raises(PermissionDenied):
            require_location_access(read_only_user, location_tree["country"])
