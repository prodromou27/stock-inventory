import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

User = get_user_model()


@pytest.mark.django_db
class TestAdministratorStaffSync:
    """Regression coverage for the Prompt 9 finding: only the original
    `createsuperuser` account could ever reach Django's built-in /admin/
    site (which is how new users get created and assigned a role at all,
    per spec §14's "User and permission administration" screen — see
    apps/accounts/signals.py), since nothing else granted `is_staff`.
    """

    def test_joining_administrator_group_grants_is_staff(self):
        user = User.objects.create_user(username="newadmin", password="a-strong-test-password-123")
        assert user.is_staff is False

        user.groups.add(Group.objects.get(name="Administrator"))
        user.refresh_from_db()
        assert user.is_staff is True

    def test_leaving_administrator_group_revokes_is_staff(self):
        user = User.objects.create_user(username="exadmin", password="a-strong-test-password-123")
        admin_group = Group.objects.get(name="Administrator")
        user.groups.add(admin_group)
        user.refresh_from_db()
        assert user.is_staff is True

        user.groups.remove(admin_group)
        user.refresh_from_db()
        assert user.is_staff is False

    def test_non_administrator_group_does_not_grant_is_staff(self):
        user = User.objects.create_user(username="manager1b", password="a-strong-test-password-123")
        user.groups.add(Group.objects.get(name="StockManager"))
        user.refresh_from_db()
        assert user.is_staff is False

    def test_superuser_is_staff_is_never_revoked(self):
        user = User.objects.create_user(
            username="super1",
            password="a-strong-test-password-123",
            is_superuser=True,
            is_staff=True,
        )
        user.groups.set([])
        user.refresh_from_db()
        assert user.is_staff is True
