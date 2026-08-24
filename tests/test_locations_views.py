import pytest
from django.urls import reverse

from apps.accounts.services import grant_location_access
from apps.locations.models import Location


@pytest.mark.django_db
class TestLocationListView:
    def test_anonymous_redirected_to_login(self, client):
        response = client.get(reverse("locations:list"))
        assert response.status_code == 302

    def test_user_sees_only_scoped_locations(
        self, client, administrator, read_only_user, location_tree, other_location_tree
    ):
        grant_location_access(
            user=read_only_user, location=location_tree["floor"], granted_by=administrator
        )

        client.force_login(read_only_user)
        response = client.get(reverse("locations:list"), {"show_inactive": "1"})

        names = {loc.name for loc in response.context["locations"]}
        assert location_tree["room"].name in names
        assert other_location_tree["country"].name not in names
        assert location_tree["country"].name not in names

    def test_administrator_sees_every_tree(
        self, client, administrator, location_tree, other_location_tree
    ):
        client.force_login(administrator)
        response = client.get(reverse("locations:list"), {"show_inactive": "1"})

        names = {loc.name for loc in response.context["locations"]}
        assert other_location_tree["country"].name in names


@pytest.mark.django_db
class TestLocationDetailView:
    def test_out_of_scope_direct_url_is_denied(
        self, client, administrator, read_only_user, location_tree, other_location_tree
    ):
        grant_location_access(
            user=read_only_user, location=location_tree["floor"], granted_by=administrator
        )

        client.force_login(read_only_user)
        response = client.get(
            reverse("locations:detail", kwargs={"pk": other_location_tree["country"].pk})
        )
        assert response.status_code == 403

    def test_in_scope_direct_url_is_allowed(
        self, client, administrator, read_only_user, location_tree
    ):
        grant_location_access(
            user=read_only_user, location=location_tree["floor"], granted_by=administrator
        )

        client.force_login(read_only_user)
        response = client.get(reverse("locations:detail", kwargs={"pk": location_tree["room"].pk}))
        assert response.status_code == 200

    def test_ancestor_of_granted_location_is_denied(
        self, client, administrator, read_only_user, location_tree
    ):
        grant_location_access(
            user=read_only_user, location=location_tree["floor"], granted_by=administrator
        )

        client.force_login(read_only_user)
        response = client.get(
            reverse("locations:detail", kwargs={"pk": location_tree["country"].pk})
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestLocationMutationPermissions:
    def test_read_only_user_cannot_create_location(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.post(reverse("locations:create"), {"level": "country", "name": "Nope"})
        assert response.status_code == 403
        assert not Location.objects.filter(name="Nope").exists()

    def test_stock_manager_cannot_create_location(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.post(reverse("locations:create"), {"level": "country", "name": "Nope"})
        assert response.status_code == 403

    def test_administrator_can_create_location(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            reverse("locations:create"), {"level": "country", "name": "New Country"}
        )
        assert response.status_code == 302
        assert Location.objects.filter(name="New Country").exists()

    def test_invalid_hierarchy_shows_form_error_instead_of_500(
        self, client, administrator, location_tree
    ):
        client.force_login(administrator)
        response = client.post(
            reverse("locations:create"),
            {"level": "site", "name": "Bad", "parent": location_tree["room"].pk},
        )
        assert response.status_code == 200
        assert response.context["form"].errors

    def test_read_only_user_cannot_toggle_active(self, client, read_only_user, location_tree):
        client.force_login(read_only_user)
        response = client.post(
            reverse("locations:toggle_active", kwargs={"pk": location_tree["room"].pk})
        )
        assert response.status_code == 403
        location_tree["room"].refresh_from_db()
        assert location_tree["room"].is_active is True

    def test_administrator_can_toggle_active(self, client, administrator, location_tree):
        client.force_login(administrator)
        response = client.post(
            reverse("locations:toggle_active", kwargs={"pk": location_tree["room"].pk})
        )
        assert response.status_code == 302
        location_tree["room"].refresh_from_db()
        assert location_tree["room"].is_active is False
