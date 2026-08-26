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

    def test_sort_by_name_descending(
        self, client, administrator, location_tree, other_location_tree
    ):
        # A level filter is what still uses the flat, sortable table (see
        # TestLocationListTreeMode below for the default tree view, which
        # doesn't use ?sort= — a level filter and a tree don't compose).
        client.force_login(administrator)
        response = client.get(
            reverse("locations:list"),
            {"level": "site", "show_inactive": "1", "sort": "name", "dir": "desc"},
        )
        names = [loc.name for loc in response.context["locations"]]
        # "Other HQ" sorts after "HQ" ascending, so descending puts it first.
        assert names.index("Other HQ") < names.index("HQ")

    def test_unknown_sort_key_falls_back_to_default(self, client, administrator, location_tree):
        client.force_login(administrator)
        response = client.get(
            reverse("locations:list"), {"level": "storage_room", "sort": "not-a-field"}
        )
        assert response.status_code == 200


@pytest.mark.django_db
class TestLocationListTreeMode:
    """The default (no ?level=) view — a nested tree, not the flat table."""

    def test_default_view_is_tree_mode(self, client, administrator, location_tree):
        client.force_login(administrator)
        response = client.get(reverse("locations:list"))
        assert response.context["is_tree_mode"] is True

    def test_level_filter_switches_to_flat_mode(self, client, administrator, location_tree):
        client.force_login(administrator)
        response = client.get(reverse("locations:list"), {"level": "storage_room"})
        assert response.context["is_tree_mode"] is False
        assert response.context["location_tree"] == []  # not built in flat mode

    def test_tree_roots_and_children_are_alphabetical(
        self, client, administrator, location_tree, other_location_tree
    ):
        client.force_login(administrator)
        response = client.get(reverse("locations:list"), {"show_inactive": "1"})
        tree = response.context["location_tree"]
        root_names = [node["location"].name for node in tree]
        assert root_names == sorted(root_names)
        assert "Elsewhere" in root_names and "Wonderland" in root_names

        wonderland_node = next(n for n in tree if n["location"].name == "Wonderland")
        assert [c["location"].name for c in wonderland_node["children"]] == ["HQ"]

    def test_scoped_stock_manager_tree_roots_at_their_granted_node(
        self, client, stock_manager_with_room_access, location_tree
    ):
        # A Stock Manager granted only "Room A" never sees the Country/Site/
        # Floor above it (apps.locations.scoping) — their tree roots at the
        # granted node itself, not at a Country-level node.
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("locations:list"))
        tree = response.context["location_tree"]
        assert [node["location"].name for node in tree] == ["Room A"]


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

    def test_stock_manager_can_create_room_in_assigned_country(
        self, client, administrator, stock_manager, location_tree
    ):
        grant_location_access(
            user=stock_manager, location=location_tree["country"], granted_by=administrator
        )
        client.force_login(stock_manager)
        response = client.post(
            reverse("locations:create"),
            {
                "level": Location.Level.STORAGE_ROOM,
                "name": "Manager Room",
                "parent": location_tree["floor"].pk,
            },
        )
        assert response.status_code == 302
        assert Location.objects.filter(name="Manager Room", parent=location_tree["floor"]).exists()

    def test_stock_manager_cannot_create_room_outside_assigned_country(
        self, client, administrator, stock_manager, location_tree, other_location_tree
    ):
        grant_location_access(
            user=stock_manager, location=location_tree["country"], granted_by=administrator
        )
        client.force_login(stock_manager)
        response = client.post(
            reverse("locations:create"),
            {
                "level": Location.Level.STORAGE_ROOM,
                "name": "Out of scope",
                "parent": other_location_tree["site"].pk,
            },
        )
        assert response.status_code == 200
        assert not Location.objects.filter(name="Out of scope").exists()

    def test_stock_manager_can_edit_and_deactivate_in_scope_room(
        self, client, administrator, stock_manager, location_tree
    ):
        grant_location_access(
            user=stock_manager, location=location_tree["country"], granted_by=administrator
        )
        client.force_login(stock_manager)
        edit_url = reverse("locations:edit", kwargs={"pk": location_tree["room"].pk})
        response = client.post(edit_url, {"name": "Secure Room", "code": "SEC"})
        assert response.status_code == 302
        location_tree["room"].refresh_from_db()
        assert (location_tree["room"].name, location_tree["room"].code) == ("Secure Room", "SEC")

        response = client.post(
            reverse("locations:toggle_active", kwargs={"pk": location_tree["room"].pk})
        )
        assert response.status_code == 302
        location_tree["room"].refresh_from_db()
        assert location_tree["room"].is_active is False

    def test_read_only_user_cannot_edit_direct_url(self, client, read_only_user, location_tree):
        client.force_login(read_only_user)
        response = client.post(
            reverse("locations:edit", kwargs={"pk": location_tree["room"].pk}),
            {"name": "Nope", "code": ""},
        )
        assert response.status_code == 403
