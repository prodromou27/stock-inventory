import pytest
from django.urls import reverse

from apps.accounts.models import UserLocationAccess
from apps.accounts.services import grant_location_access, revoke_location_access
from apps.audit.models import AuditEvent


@pytest.mark.django_db
class TestUserAccessListView:
    def test_stock_manager_cannot_view_access_list(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.get(reverse("accounts:user_access_list"))
        assert response.status_code == 403

    def test_read_only_user_cannot_view_access_list(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("accounts:user_access_list"))
        assert response.status_code == 403

    def test_administrator_can_view_access_list(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("accounts:user_access_list"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestGrantAndRevokeViews:
    def test_read_only_user_cannot_grant_access(self, client, read_only_user, location_tree):
        client.force_login(read_only_user)
        response = client.post(
            reverse("accounts:grant_access"),
            {"user": read_only_user.pk, "location": location_tree["room"].pk},
        )
        assert response.status_code == 403
        assert not UserLocationAccess.objects.filter(user=read_only_user).exists()

    def test_administrator_can_grant_and_revoke_access(
        self, client, administrator, stock_manager, location_tree
    ):
        client.force_login(administrator)

        response = client.post(
            reverse("accounts:grant_access"),
            {"user": stock_manager.pk, "location": location_tree["country"].pk},
        )
        assert response.status_code == 302
        access = UserLocationAccess.objects.get(
            user=stock_manager, location=location_tree["country"]
        )

        response = client.post(reverse("accounts:revoke_access", kwargs={"pk": access.pk}))
        assert response.status_code == 302
        assert not UserLocationAccess.objects.filter(pk=access.pk).exists()

    def test_grant_form_only_accepts_country_scope(
        self, client, administrator, stock_manager, location_tree
    ):
        client.force_login(administrator)
        response = client.post(
            reverse("accounts:grant_access"),
            {"user": stock_manager.pk, "location": location_tree["room"].pk},
        )
        assert response.status_code == 200
        assert "Select a valid choice" in response.content.decode()

    def test_stock_manager_cannot_revoke_access(
        self, client, administrator, stock_manager, location_tree
    ):
        access = grant_location_access(
            user=stock_manager, location=location_tree["room"], granted_by=administrator
        )

        client.force_login(stock_manager)
        response = client.post(reverse("accounts:revoke_access", kwargs={"pk": access.pk}))
        assert response.status_code == 403
        assert UserLocationAccess.objects.filter(pk=access.pk).exists()


@pytest.mark.django_db
class TestAccessAuditing:
    def test_grant_is_audited(self, administrator, stock_manager, location_tree):
        access = grant_location_access(
            user=stock_manager, location=location_tree["room"], granted_by=administrator
        )

        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.PERMISSION_CHANGED,
            object_id=str(access.pk),
        ).exists()

    def test_revoke_is_audited(self, administrator, stock_manager, location_tree):
        access = grant_location_access(
            user=stock_manager, location=location_tree["room"], granted_by=administrator
        )
        revoke_location_access(access=access, revoked_by=administrator)

        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.PERMISSION_CHANGED,
            summary__icontains="Revoked",
        ).exists()

    def test_granting_same_access_twice_is_idempotent_and_audited_once(
        self, administrator, stock_manager, location_tree
    ):
        grant_location_access(
            user=stock_manager, location=location_tree["room"], granted_by=administrator
        )
        grant_location_access(
            user=stock_manager, location=location_tree["room"], granted_by=administrator
        )

        assert (
            UserLocationAccess.objects.filter(
                user=stock_manager, location=location_tree["room"]
            ).count()
            == 1
        )
        assert (
            AuditEvent.objects.filter(
                event_type=AuditEvent.EventType.PERMISSION_CHANGED,
                summary__icontains="Granted",
            ).count()
            == 1
        )
