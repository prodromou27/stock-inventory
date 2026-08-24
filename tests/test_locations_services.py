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
