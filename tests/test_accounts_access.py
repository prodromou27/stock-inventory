import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.models import MustChangePassword, UserLocationAccess
from apps.accounts.services import (
    create_user,
    grant_location_access,
    revoke_location_access,
    set_user_active,
    set_user_role,
)
from apps.audit.models import AuditEvent
from apps.core.authorization import ADMINISTRATOR, READ_ONLY_USER, STOCK_MANAGER

User = get_user_model()


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


@pytest.mark.django_db
class TestCreateUser:
    def test_administrator_can_create_a_user_with_a_role(self, administrator):
        user = create_user(
            created_by=administrator,
            username="newmanager",
            password="a-strong-password-123",
            role=STOCK_MANAGER,
        )
        assert user.groups.filter(name=STOCK_MANAGER).exists()
        assert user.check_password("a-strong-password-123")

    def test_created_user_must_change_password_on_first_login(self, administrator):
        user = create_user(
            created_by=administrator,
            username="newreader",
            password="a-strong-password-123",
            role=READ_ONLY_USER,
        )
        assert MustChangePassword.objects.filter(user=user).exists()

    def test_creation_is_audited(self, administrator):
        create_user(
            created_by=administrator,
            username="newadmin",
            password="a-strong-password-123",
            role=ADMINISTRATOR,
        )
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.RECORD_CREATED,
            summary__icontains="newadmin",
        ).exists()

    def test_duplicate_username_rejected(self, administrator, stock_manager):
        with pytest.raises(ValidationError):
            create_user(
                created_by=administrator,
                username=stock_manager.username,
                password="a-strong-password-123",
                role=READ_ONLY_USER,
            )

    def test_invalid_role_rejected(self, administrator):
        with pytest.raises(ValidationError):
            create_user(
                created_by=administrator,
                username="somebody",
                password="a-strong-password-123",
                role="NotARealRole",
            )

    def test_non_administrator_cannot_create_user(self, stock_manager):
        with pytest.raises(PermissionDenied):
            create_user(
                created_by=stock_manager,
                username="somebody",
                password="a-strong-password-123",
                role=READ_ONLY_USER,
            )
        assert not User.objects.filter(username="somebody").exists()

    def test_view_requires_administrator(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.post(
            reverse("accounts:create_user"),
            {"username": "x", "password": "a-strong-password-123", "role": READ_ONLY_USER},
        )
        assert response.status_code == 403

    def test_view_creates_user_and_redirects(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            reverse("accounts:create_user"),
            {"username": "viewcreated", "password": "a-strong-password-123", "role": STOCK_MANAGER},
        )
        assert response.status_code == 302
        assert User.objects.filter(username="viewcreated").exists()

    def test_view_rejects_weak_password_with_form_error(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            reverse("accounts:create_user"),
            {"username": "weakpw", "password": "short", "role": READ_ONLY_USER},
        )
        assert response.status_code == 200
        assert not User.objects.filter(username="weakpw").exists()


@pytest.mark.django_db
class TestSetUserRole:
    def test_administrator_can_change_role(self, administrator, stock_manager):
        set_user_role(user=stock_manager, role=READ_ONLY_USER, changed_by=administrator)
        stock_manager.refresh_from_db()
        assert stock_manager.groups.filter(name=READ_ONLY_USER).exists()
        assert not stock_manager.groups.filter(name=STOCK_MANAGER).exists()

    def test_role_change_replaces_not_accumulates(self, administrator, stock_manager):
        set_user_role(user=stock_manager, role=READ_ONLY_USER, changed_by=administrator)
        set_user_role(user=stock_manager, role=ADMINISTRATOR, changed_by=administrator)
        stock_manager.refresh_from_db()
        role_names = set(stock_manager.groups.values_list("name", flat=True))
        assert role_names == {ADMINISTRATOR}

    def test_setting_the_same_role_is_a_noop_not_audited_again(self, administrator, stock_manager):
        before = AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.PERMISSION_CHANGED
        ).count()
        set_user_role(user=stock_manager, role=STOCK_MANAGER, changed_by=administrator)
        after = AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.PERMISSION_CHANGED
        ).count()
        assert after == before

    def test_role_change_is_audited(self, administrator, stock_manager):
        set_user_role(user=stock_manager, role=ADMINISTRATOR, changed_by=administrator)
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.PERMISSION_CHANGED,
            new_values={"role": ADMINISTRATOR},
        ).exists()

    def test_non_administrator_cannot_change_role(self, stock_manager, read_only_user):
        with pytest.raises(PermissionDenied):
            set_user_role(user=read_only_user, role=ADMINISTRATOR, changed_by=stock_manager)

    def test_view_requires_administrator(self, client, stock_manager, read_only_user):
        client.force_login(stock_manager)
        response = client.post(
            reverse("accounts:set_user_role", args=[read_only_user.pk]), {"role": ADMINISTRATOR}
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestSetUserActive:
    def test_administrator_can_deactivate_and_reactivate(self, administrator, stock_manager):
        set_user_active(user=stock_manager, is_active=False, changed_by=administrator)
        stock_manager.refresh_from_db()
        assert stock_manager.is_active is False

        set_user_active(user=stock_manager, is_active=True, changed_by=administrator)
        stock_manager.refresh_from_db()
        assert stock_manager.is_active is True

    def test_deactivated_user_cannot_log_in(self, client, administrator, stock_manager):
        # client.login() calls authenticate() with no request object, which
        # django-axes' backend rejects outright — go through the real login
        # view instead, same precedent as tests/test_auth.py.
        set_user_active(user=stock_manager, is_active=False, changed_by=administrator)
        response = client.post(
            reverse("login"),
            {"username": stock_manager.username, "password": "a-strong-test-password-123"},
        )
        assert response.status_code == 200  # re-renders the login form, not a redirect
        assert not response.wsgi_request.user.is_authenticated

    def test_deactivation_is_audited(self, administrator, stock_manager):
        set_user_active(user=stock_manager, is_active=False, changed_by=administrator)
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.RECORD_UPDATED,
            new_values={"is_active": False},
        ).exists()

    def test_non_administrator_cannot_deactivate(self, stock_manager, read_only_user):
        with pytest.raises(PermissionDenied):
            set_user_active(user=read_only_user, is_active=False, changed_by=stock_manager)

    def test_account_row_preserved_after_deactivation(self, administrator, stock_manager):
        """Deactivating must never delete the account — it's still referenced
        by on_delete=PROTECT everywhere in the append-only ledger, so
        deletion was never actually a safe option once a user has performed
        any transaction.
        """
        set_user_active(user=stock_manager, is_active=False, changed_by=administrator)
        assert User.objects.filter(pk=stock_manager.pk).exists()

    def test_view_requires_administrator(self, client, stock_manager, read_only_user):
        client.force_login(stock_manager)
        response = client.post(reverse("accounts:toggle_user_active", args=[read_only_user.pk]))
        assert response.status_code == 403
