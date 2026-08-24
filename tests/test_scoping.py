import pytest
from django.core.exceptions import PermissionDenied

from apps.accounts.services import grant_location_access
from apps.locations.models import Location
from apps.locations.scoping import accessible_locations, require_location_access, scope_queryset


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
