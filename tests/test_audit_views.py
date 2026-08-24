from datetime import date

import pytest
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.inventory.services.receipts import receive_stock


@pytest.mark.django_db
class TestAuditLogListView:
    def test_anonymous_redirected(self, client):
        response = client.get(reverse("audit:log"))
        assert response.status_code == 302

    def test_read_only_user_forbidden(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("audit:log"))
        assert response.status_code == 403

    def test_stock_manager_forbidden(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.get(reverse("audit:log"))
        assert response.status_code == 403

    def test_administrator_can_view(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-AUDIT-1",
        )

        client.force_login(administrator)
        response = client.get(reverse("audit:log"))
        assert response.status_code == 200
        assert response.context["events"].count() > 0

    def test_event_type_filter(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-AUDIT-2",
        )

        client.force_login(administrator)
        response = client.get(
            reverse("audit:log"), {"event_type": AuditEvent.EventType.LOGIN_SUCCESS}
        )
        for event in response.context["events"]:
            assert event.event_type == AuditEvent.EventType.LOGIN_SUCCESS

    def test_actor_filter(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-AUDIT-3",
        )

        client.force_login(administrator)
        response = client.get(reverse("audit:log"), {"actor": "nonexistent-user-xyz"})
        assert response.context["events"].count() == 0
