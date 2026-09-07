import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.inventory.forms import ReceiveStockForm
from apps.locations.models import Location
from apps.locations.scoping import require_room_or_below
from apps.locations.services import create_location


@pytest.mark.django_db
class TestRequireRoomOrBelow:
    def test_country_is_rejected(self, location_tree):
        with pytest.raises(ValidationError):
            require_room_or_below(location_tree["country"])

    def test_storage_room_is_accepted(self, location_tree):
        require_room_or_below(location_tree["room"])  # must not raise

    def test_rack_shelf_is_accepted(self, administrator, location_tree):
        shelf = create_location(
            level=Location.Level.RACK_SHELF,
            name="Shelf A",
            parent=location_tree["room"],
            user=administrator,
        )
        require_room_or_below(shelf)  # must not raise

    def test_none_is_accepted(self):
        require_room_or_below(None)  # a separate "is it required at all" concern


@pytest.mark.django_db
class TestEmptyRoomHint:
    """A user with no accessible Storage Room (a brand-new install with no
    locations yet, or access granted only above room level) gets an empty
    <select> — apps.inventory.forms._apply_scoped_room_location() flags
    this so templates/_form_field.html can explain why instead of leaving
    an unexplained blank dropdown.
    """

    def test_hint_set_when_no_room_is_accessible(self, administrator):
        form = ReceiveStockForm(user=administrator)
        assert not form.fields["location"].queryset.exists()
        assert "No Storage Room" in form.fields["location"].widget.attrs["data_empty_hint"]

    def test_hint_absent_once_a_room_is_accessible(self, administrator, location_tree):
        form = ReceiveStockForm(user=administrator)
        assert form.fields["location"].queryset.exists()
        assert "data_empty_hint" not in form.fields["location"].widget.attrs


@pytest.mark.django_db
class TestRoomOptionsForCountryView:
    def test_returns_rooms_under_the_selected_country(self, client, administrator, location_tree):
        client.force_login(administrator)
        response = client.get(
            reverse("locations:rooms_for_country"), {"country": location_tree["country"].pk}
        )
        assert response.status_code == 200
        names = {r["name"] for r in response.json()["rooms"]}
        assert names == {"Room A"}

    def test_scoped_to_accessible_countries(
        self, client, stock_manager_with_room_access, location_tree, other_location_tree
    ):
        client.force_login(stock_manager_with_room_access)
        response = client.get(
            reverse("locations:rooms_for_country"), {"country": other_location_tree["country"].pk}
        )
        assert response.json()["rooms"] == []

    def test_empty_country_returns_no_rooms(self, client, administrator):
        empty_country = create_location(
            level=Location.Level.COUNTRY, name="No Rooms Yet", user=administrator
        )
        client.force_login(administrator)
        response = client.get(reverse("locations:rooms_for_country"), {"country": empty_country.pk})
        assert response.json()["rooms"] == []

    def test_missing_country_param_returns_empty(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("locations:rooms_for_country"))
        assert response.json()["rooms"] == []


@pytest.mark.django_db
class TestShelfOptionsForRoomView:
    def test_returns_racks_and_shelves_under_the_room(self, client, administrator, location_tree):
        """Rack/Shelf is a single, flat leaf level directly under the room
        now (no more nesting a Shelf under a Rack), so every label here is
        just the node's own name.
        """
        create_location(
            level=Location.Level.RACK_SHELF,
            name="Rack 1",
            parent=location_tree["room"],
            user=administrator,
        )
        create_location(
            level=Location.Level.RACK_SHELF,
            name="Shelf A",
            parent=location_tree["room"],
            user=administrator,
        )
        client.force_login(administrator)
        response = client.get(
            reverse("locations:shelves_for_room"), {"room": location_tree["room"].pk}
        )
        shelves = response.json()["shelves"]
        labels = {s["name"] for s in shelves}
        assert "Rack 1" in labels
        assert "Shelf A" in labels

    def test_scoped_to_accessible_rooms(
        self, client, stock_manager_with_room_access, other_location_tree, administrator
    ):
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Shelf Test Other Room",
            parent=other_location_tree["country"],
            user=administrator,
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("locations:shelves_for_room"), {"room": other_room.pk})
        assert response.json()["shelves"] == []
