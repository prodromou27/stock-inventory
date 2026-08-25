import pytest

from apps.imports.location_resolution import resolve_location
from apps.locations.models import Location
from apps.locations.services import create_location


@pytest.mark.django_db
class TestResolveLocation:
    def test_no_location_text_is_unresolved(self):
        location, detail = resolve_location("", "")
        assert location is None
        assert "No location given" in detail

    def test_unknown_location_is_unresolved(self, location_tree):
        location, detail = resolve_location("Nowhere", "")
        assert location is None
        assert "Unknown location" in detail

    def test_exact_name_match_with_no_sub_location(self, location_tree):
        location, detail = resolve_location("Room A", "")
        assert location == location_tree["room"]
        assert detail == ""

    def test_case_insensitive_match(self, location_tree):
        location, detail = resolve_location("room a", "")
        assert location == location_tree["room"]

    def test_sub_location_narrows_to_child_by_name(self, administrator, location_tree):
        rack = create_location(
            level=Location.Level.RACK_CABINET,
            name="8",
            parent=location_tree["room"],
            user=administrator,
        )
        location, detail = resolve_location("Room A", "8")
        assert location == rack
        assert detail == ""

    def test_sub_location_narrows_to_child_by_code(self, administrator, location_tree):
        rack = create_location(
            level=Location.Level.RACK_CABINET,
            name="Rack Seven",
            parent=location_tree["room"],
            user=administrator,
        )
        rack.code = "7"
        rack.save(update_fields=["code"])
        location, detail = resolve_location("Room A", "7")
        assert location == rack

    def test_sub_location_not_found_falls_back_to_parent(self, location_tree):
        location, detail = resolve_location("Room A", "99")
        assert location == location_tree["room"]
        assert "not found" in detail

    def test_ambiguous_top_level_name_is_unresolved(
        self, administrator, location_tree, other_location_tree
    ):
        from apps.locations.models import Location as LocationModel

        other_floor = create_location(
            level=LocationModel.Level.FLOOR,
            name="Other Floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        create_location(
            level=LocationModel.Level.STORAGE_ROOM,
            name="Room A",
            parent=other_floor,
            user=administrator,
        )
        location, detail = resolve_location("Room A", "")
        assert location is None
        assert "matches 2 different locations" in detail
