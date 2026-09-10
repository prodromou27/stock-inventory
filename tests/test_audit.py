import pytest
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.audit.services import record_event


@pytest.mark.django_db
class TestAuditEventAppendOnly:
    def test_administrator_can_view_field_changes_and_record_link(
        self, client, administrator, unit_product
    ):
        event = record_event(
            actor=administrator,
            event_type=AuditEvent.EventType.RECORD_UPDATED,
            obj=unit_product,
            summary="Updated product",
            old_values={"supplier": "Old"},
            new_values={"supplier": "New"},
        )
        client.force_login(administrator)
        response = client.get(reverse("audit:detail", args=[event.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Previous values" in content
        assert "Old" in content and "New" in content
        assert reverse("catalog:product_detail", args=[unit_product.pk]) in content

    def test_non_administrator_cannot_view_event_detail(self, client, stock_manager, administrator):
        event = record_event(
            actor=administrator, event_type=AuditEvent.EventType.RECORD_CREATED, summary="Private"
        )
        client.force_login(stock_manager)
        assert client.get(reverse("audit:detail", args=[event.pk])).status_code == 403

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
