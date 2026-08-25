import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


@pytest.mark.django_db
def test_locks_out_after_failure_limit(client):
    User.objects.create_user(username="jane", password="a-strong-test-password-123")

    for _ in range(settings.AXES_FAILURE_LIMIT):
        response = client.post(reverse("login"), {"username": "jane", "password": "wrong"})

    assert response.status_code == 429


@pytest.mark.django_db
def test_locked_account_rejects_even_correct_password(client):
    User.objects.create_user(username="jane", password="a-strong-test-password-123")

    for _ in range(settings.AXES_FAILURE_LIMIT):
        client.post(reverse("login"), {"username": "jane", "password": "wrong"})

    response = client.post(
        reverse("login"), {"username": "jane", "password": "a-strong-test-password-123"}
    )
    assert response.status_code == 429


@pytest.mark.django_db
def test_lockout_is_per_username_not_global(client):
    """A different account must still be able to log in while another
    account is locked out — locking is scoped to (username, ip_address),
    not the whole IP, per docs/architecture/08-nonfunctional-plan.md.
    """
    User.objects.create_user(username="jane", password="a-strong-test-password-123")
    User.objects.create_user(username="bob", password="another-strong-password-456")

    for _ in range(settings.AXES_FAILURE_LIMIT):
        client.post(reverse("login"), {"username": "jane", "password": "wrong"})

    response = client.post(
        reverse("login"), {"username": "bob", "password": "another-strong-password-456"}
    )
    assert response.status_code == 302


@pytest.mark.django_db
def test_below_failure_limit_does_not_lock_out(client):
    User.objects.create_user(username="jane", password="a-strong-test-password-123")

    for _ in range(settings.AXES_FAILURE_LIMIT - 1):
        client.post(reverse("login"), {"username": "jane", "password": "wrong"})

    response = client.post(
        reverse("login"), {"username": "jane", "password": "a-strong-test-password-123"}
    )
    assert response.status_code == 302
