import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_content_security_policy_header_is_set(client, administrator):
    """apps.core.csp.ContentSecurityPolicyMiddleware — a baseline CSP as
    defense-in-depth (no known XSS sink; every static asset is same-origin).
    """
    client.force_login(administrator)
    response = client.get(reverse("core:home"))
    assert "Content-Security-Policy" in response
    policy = response["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy


@pytest.mark.django_db
def test_content_security_policy_header_set_even_when_logged_out(client):
    response = client.get(reverse("login"))
    assert "Content-Security-Policy" in response
