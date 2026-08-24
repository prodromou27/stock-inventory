import pytest

from apps.audit.models import AuditEvent
from apps.audit.services import record_event


@pytest.mark.django_db
class TestAuditEventAppendOnly:
    def test_cannot_update_an_existing_event_via_save(self, administrator):
        event = record_event(
            actor=administrator, event_type=AuditEvent.EventType.RECORD_CREATED, summary="a"
        )
        event.summary = "changed"
        with pytest.raises(ValueError):
            event.save()

    def test_cannot_delete_an_event_via_instance_delete(self, administrator):
        event = record_event(
            actor=administrator, event_type=AuditEvent.EventType.RECORD_CREATED, summary="a"
        )
        with pytest.raises(ValueError):
            event.delete()

    def test_cannot_bulk_update_events(self, administrator):
        record_event(
            actor=administrator, event_type=AuditEvent.EventType.RECORD_CREATED, summary="a"
        )
        with pytest.raises(ValueError):
            AuditEvent.objects.filter(actor=administrator).update(summary="changed")

    def test_cannot_bulk_delete_events(self, administrator):
        record_event(
            actor=administrator, event_type=AuditEvent.EventType.RECORD_CREATED, summary="a"
        )
        with pytest.raises(ValueError):
            AuditEvent.objects.filter(actor=administrator).delete()

    def test_record_event_captures_object_reference(self, administrator):
        event = record_event(
            actor=administrator,
            event_type=AuditEvent.EventType.RECORD_CREATED,
            obj=administrator,
            summary="created something",
        )
        assert event.object_type == administrator.__class__.__name__
        assert event.object_id == str(administrator.pk)
