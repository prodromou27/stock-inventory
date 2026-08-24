import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_health_check_returns_ok(client):
    response = client.get(reverse("core:health"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"] == "ok"
