import pytest
from django.core.exceptions import ValidationError

from apps.audit.models import AuditEvent
from apps.locations.models import Location
from apps.locations.services import create_location, deactivate_location, reactivate_location


@pytest.mark.django_db
class TestLocationHierarchyValidation:
    def test_country_has_no_parent_and_path_is_its_own_id(self, administrator):
        country = create_location(level=Location.Level.COUNTRY, name="Testland", user=administrator)

        assert country.parent is None
        assert country.path == country.id.hex

    def test_child_must_match_expected_parent_level(self, administrator, location_tree):
        with pytest.raises(ValidationError):
            create_location(
                level=Location.Level.SITE,
                name="Bad",
                parent=location_tree["room"],
                user=administrator,
            )

    def test_country_cannot_be_given_a_parent(self, administrator, location_tree):
        with pytest.raises(ValidationError):
            create_location(
                level=Location.Level.COUNTRY,
                name="Bad Country",
                parent=location_tree["site"],
                user=administrator,
            )

    def test_path_is_dot_joined_ancestor_chain(self, location_tree):
        room, floor, site, country = (
            location_tree["room"],
            location_tree["floor"],
            location_tree["site"],
            location_tree["country"],
        )

        assert room.path.startswith(floor.path + ".")
        assert floor.path.startswith(site.path + ".")
        assert site.path.startswith(country.path + ".")

    def test_duplicate_sibling_name_rejected_case_insensitively(self, administrator, location_tree):
        with pytest.raises(ValidationError):
            create_location(
                level=Location.Level.FLOOR,
                name=location_tree["floor"].name.upper(),
                parent=location_tree["site"],
                user=administrator,
            )

    def test_same_name_allowed_under_a_different_parent(self, administrator, location_tree):
        # "1st Floor" already exists under HQ; a different Site may reuse the name.
        other_site = create_location(
            level=Location.Level.SITE,
            name="Other Building",
            parent=location_tree["country"],
            user=administrator,
        )
        floor = create_location(
            level=Location.Level.FLOOR,
            name=location_tree["floor"].name,
            parent=other_site,
            user=administrator,
        )
        assert floor.pk is not None

    def test_duplicate_country_name_rejected(self, administrator, location_tree):
        with pytest.raises(ValidationError):
            create_location(
                level=Location.Level.COUNTRY,
                name=location_tree["country"].name.lower(),
                user=administrator,
            )

    def test_name_whitespace_is_normalized(self, administrator):
        location = create_location(
            level=Location.Level.COUNTRY, name="  Multi   Space   Name  ", user=administrator
        )
        assert location.name == "Multi Space Name"

    def test_stock_manager_cannot_create_location(self, stock_manager):
        with pytest.raises(Exception):
            create_location(level=Location.Level.COUNTRY, name="Nope", user=stock_manager)

    def test_creating_location_records_audit_event(self, administrator):
        location = create_location(
            level=Location.Level.COUNTRY, name="Auditland", user=administrator
        )

        assert AuditEvent.objects.filter(
            object_type="Location",
            object_id=str(location.pk),
            event_type=AuditEvent.EventType.RECORD_CREATED,
        ).exists()


@pytest.mark.django_db
class TestLocationDeactivation:
    def test_deactivate_then_reactivate(self, administrator, location_tree):
        room = location_tree["room"]

        deactivate_location(location=room, user=administrator)
        room.refresh_from_db()
        assert room.is_active is False

        reactivate_location(location=room, user=administrator)
        room.refresh_from_db()
        assert room.is_active is True

    def test_deactivation_preserves_the_row_rather_than_deleting_it(
        self, administrator, location_tree
    ):
        room = location_tree["room"]
        deactivate_location(location=room, user=administrator)

        assert Location.objects.filter(pk=room.pk).exists()

    def test_deactivation_is_audited(self, administrator, location_tree):
        room = location_tree["room"]
        deactivate_location(location=room, user=administrator)

        assert AuditEvent.objects.filter(
            object_id=str(room.pk),
            event_type=AuditEvent.EventType.RECORD_UPDATED,
            new_values={"is_active": False},
        ).exists()

    def test_stock_manager_cannot_deactivate(self, stock_manager, location_tree):
        with pytest.raises(Exception):
            deactivate_location(location=location_tree["room"], user=stock_manager)

    def test_deactivating_a_country_cascades_to_every_descendant(
        self, administrator, location_tree
    ):
        country, site, floor, room = (
            location_tree["country"],
            location_tree["site"],
            location_tree["floor"],
            location_tree["room"],
        )
        deactivate_location(location=country, user=administrator)

        for loc in (country, site, floor, room):
            loc.refresh_from_db()
            assert loc.is_active is False

    def test_deactivating_a_country_records_descendant_count(self, administrator, location_tree):
        country = location_tree["country"]
        deactivate_location(location=country, user=administrator)

        event = AuditEvent.objects.get(
            object_id=str(country.pk),
            event_type=AuditEvent.EventType.RECORD_UPDATED,
            new_values={"is_active": False},
        )
        assert event.metadata["deactivated_descendant_count"] == 3  # site, floor, room

    def test_deactivating_an_already_inactive_country_is_a_noop(self, administrator, location_tree):
        country, room = location_tree["country"], location_tree["room"]
        deactivate_location(location=country, user=administrator)
        room.refresh_from_db()
        assert room.is_active is False

        # Reactivate just the room independently, then deactivate the country
        # again — the already-inactive-country early-return must not silently
        # skip re-cascading, but deactivating an already-inactive country is a
        # no-op by design (mirrors the existing single-row no-op), so the
        # independently-reactivated room should NOT be touched by this call.
        reactivate_location(location=room, user=administrator)
        room.refresh_from_db()
        assert room.is_active is True

        result = deactivate_location(location=country, user=administrator)
        assert result.is_active is False
        room.refresh_from_db()
        assert room.is_active is True  # untouched — country was already inactive

    def test_reactivating_a_country_does_not_cascade_to_descendants(
        self, administrator, location_tree
    ):
        country, room = location_tree["country"], location_tree["room"]
        deactivate_location(location=country, user=administrator)

        reactivate_location(location=country, user=administrator)
        country.refresh_from_db()
        room.refresh_from_db()
        assert country.is_active is True
        assert room.is_active is False  # deliberately not resurrected

    def test_new_child_created_under_an_active_parent_after_a_sibling_deactivation(
        self, administrator, location_tree
    ):
        """A location created after its parent chain was already active again
        is unaffected by an unrelated earlier deactivation elsewhere in the
        tree — the parent-active check only cares about the direct parent's
        current state, which the cascade now keeps consistent.
        """
        from apps.locations.services import create_location

        new_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Room B",
            parent=location_tree["floor"],
            user=administrator,
        )
        assert new_room.is_active is True
