import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from apps.accounts.models import MustChangePassword
from apps.core.authorization import ADMINISTRATOR

User = get_user_model()


@pytest.mark.django_db
class TestBootstrapAdminCommand:
    def test_production_rejects_missing_or_default_bootstrap_password(self, monkeypatch):
        monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.production")
        monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
        with pytest.raises(CommandError, match="non-default"):
            call_command("bootstrap_admin")
        assert not User.objects.filter(username="admin").exists()

    def test_creates_default_admin_when_none_exists(self):
        call_command("bootstrap_admin")

        user = User.objects.get(username="admin")
        assert user.check_password("admin")
        assert user.groups.filter(name=ADMINISTRATOR).exists()
        assert MustChangePassword.objects.filter(user=user).exists()

    def test_skips_when_an_administrator_already_exists(self, administrator):
        call_command("bootstrap_admin")

        assert not User.objects.filter(username="admin").exists()

    def test_skips_when_a_superuser_already_exists(self):
        User.objects.create_user(username="root", password="x", is_superuser=True)
        call_command("bootstrap_admin")

        assert not User.objects.filter(username="admin").exists()

    def test_is_idempotent_across_repeated_runs(self):
        call_command("bootstrap_admin")
        call_command("bootstrap_admin")
        call_command("bootstrap_admin")

        assert User.objects.filter(username="admin").count() == 1

    def test_does_not_reset_password_after_operator_changes_it(self):
        call_command("bootstrap_admin")
        user = User.objects.get(username="admin")
        user.set_password("a-real-strong-password-the-admin-chose")
        user.save()
        MustChangePassword.objects.filter(user=user).delete()

        call_command("bootstrap_admin")

        user.refresh_from_db()
        assert user.check_password("a-real-strong-password-the-admin-chose")
        assert not MustChangePassword.objects.filter(user=user).exists()

    def test_username_and_password_are_env_configurable(self, monkeypatch):
        monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "root")
        monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "a-custom-default")

        call_command("bootstrap_admin")

        user = User.objects.get(username="root")
        assert user.check_password("a-custom-default")

    def test_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("BOOTSTRAP_ADMIN_ENABLED", "false")

        call_command("bootstrap_admin")

        assert not User.objects.filter(username="admin").exists()


def _login_as_admin(client):
    # client.login() calls authenticate() with no request object, which
    # AxesStandaloneBackend rejects outright (it needs a real request to
    # check lockout state) — go through the real login view instead, which
    # is what actually exercises the admin/admin credentials anyway.
    return client.post(reverse("login"), {"username": "admin", "password": "admin"})


@pytest.mark.django_db
class TestForcedPasswordChangeFlow:
    def test_pending_change_blocks_every_other_page(self, client):
        call_command("bootstrap_admin")
        _login_as_admin(client)

        response = client.get(reverse("core:home"))
        assert response.status_code == 302
        assert response.url == reverse("password_change")

    def test_password_change_page_itself_is_reachable(self, client):
        call_command("bootstrap_admin")
        _login_as_admin(client)

        response = client.get(reverse("password_change"))
        assert response.status_code == 200

    def test_logout_is_reachable_while_pending(self, client):
        call_command("bootstrap_admin")
        _login_as_admin(client)

        response = client.post(reverse("logout"))
        assert response.status_code in (200, 302)

    def test_successful_change_clears_the_flag_and_unblocks_navigation(self, client):
        call_command("bootstrap_admin")
        _login_as_admin(client)

        response = client.post(
            reverse("password_change"),
            {
                "old_password": "admin",
                "new_password1": "a-genuinely-strong-new-password-123",
                "new_password2": "a-genuinely-strong-new-password-123",
            },
        )
        assert response.status_code == 302

        user = User.objects.get(username="admin")
        assert not MustChangePassword.objects.filter(user=user).exists()

        home_response = client.get(reverse("core:home"))
        assert home_response.status_code == 200

    def test_wrong_old_password_does_not_clear_the_flag(self, client):
        call_command("bootstrap_admin")
        _login_as_admin(client)

        client.post(
            reverse("password_change"),
            {
                "old_password": "wrong-password",
                "new_password1": "a-genuinely-strong-new-password-123",
                "new_password2": "a-genuinely-strong-new-password-123",
            },
        )

        user = User.objects.get(username="admin")
        assert MustChangePassword.objects.filter(user=user).exists()

    def test_ordinary_user_without_the_flag_is_never_blocked(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.get(reverse("core:home"))
        assert response.status_code == 200

    def test_voluntary_password_change_by_an_unflagged_user_still_works(
        self, client, administrator
    ):
        client.force_login(administrator)
        response = client.post(
            reverse("password_change"),
            {
                "old_password": "a-strong-test-password-123",
                "new_password1": "another-genuinely-strong-password-456",
                "new_password2": "another-genuinely-strong-password-456",
            },
        )
        assert response.status_code == 302
        assert not MustChangePassword.objects.filter(user=administrator).exists()


@pytest.mark.django_db
def test_administrator_group_exists_from_migration():
    """bootstrap_admin relies on this group already existing (created by
    apps/accounts/migrations/0001_create_role_groups.py) rather than
    get_or_create-ing it itself.
    """
    assert Group.objects.filter(name=ADMINISTRATOR).exists()
