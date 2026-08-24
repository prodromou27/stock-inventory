import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.audit.models import AuditEvent

User = get_user_model()


@pytest.mark.django_db
def test_home_redirects_anonymous_user_to_login(client):
    response = client.get(reverse("core:home"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))


@pytest.mark.django_db
def test_authenticated_user_can_reach_home(client):
    user = User.objects.create_user(username="jane", password="a-strong-test-password-123")

    client.force_login(user)
    response = client.get(reverse("core:home"))

    assert response.status_code == 200
    assert b"Dashboard" in response.content


@pytest.mark.django_db
def test_login_view_authenticates_valid_credentials(client):
    User.objects.create_user(username="jane", password="a-strong-test-password-123")

    response = client.post(
        reverse("login"),
        {"username": "jane", "password": "a-strong-test-password-123"},
    )

    assert response.status_code == 302


@pytest.mark.django_db
def test_successful_login_is_audited(client):
    User.objects.create_user(username="jane", password="a-strong-test-password-123")

    client.post(reverse("login"), {"username": "jane", "password": "a-strong-test-password-123"})

    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EventType.LOGIN_SUCCESS, actor__username="jane"
    ).exists()


@pytest.mark.django_db
def test_failed_login_is_audited(client):
    User.objects.create_user(username="jane", password="a-strong-test-password-123")

    client.post(reverse("login"), {"username": "jane", "password": "wrong-password"})

    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EventType.LOGIN_FAILURE, summary__icontains="jane"
    ).exists()
