import pytest
from django.test import RequestFactory
from django.urls import reverse
from django.views.defaults import server_error


@pytest.mark.django_db
def test_404_page_is_custom_and_has_no_traceback(client):
    response = client.get("/this-page-does-not-exist/")
    assert response.status_code == 404
    body = response.content.decode()
    assert "Page not found" in body
    assert "Traceback" not in body


@pytest.mark.django_db
def test_403_page_is_custom_and_has_no_traceback(client, stock_manager):
    client.force_login(stock_manager)
    response = client.get(reverse("audit:log"))
    assert response.status_code == 403
    body = response.content.decode()
    assert "Access denied" in body
    assert "Traceback" not in body


def test_500_page_is_custom_and_has_no_traceback():
    """Exercises the actual server_error()/500.html pairing Django invokes
    for an unhandled exception with DEBUG=False (doc 08: "custom 500/403/404
    pages that never leak a traceback"). Calling the view directly, rather
    than routing a real exception through a throwaway URL, keeps this
    isolated from urls.py.
    """
    request = RequestFactory().get("/")
    response = server_error(request)
    assert response.status_code == 500
    body = response.content.decode()
    assert "Something went wrong" in body
    assert "Traceback" not in body
    assert 'File "' not in body
