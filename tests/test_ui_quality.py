import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse


def test_local_ui_assets_are_discoverable():
    for asset in ("css/app.css", "js/app.js", "icons/sprite.svg", "icons/favicon.svg"):
        assert finders.find(asset), f"Missing collected static asset: {asset}"


@pytest.mark.django_db
def test_authenticated_shell_has_accessible_navigation_and_local_assets(client, administrator):
    client.force_login(administrator)
    response = client.get(reverse("core:home"))
    html = response.content.decode()
    assert response.status_code == 200
    assert 'aria-label="Primary navigation"' in html
    assert 'href="/static/css/app.css"' in html
    assert 'src="/static/js/app.js"' in html
    assert 'class="user-menu"' in html
    assert "https://" not in html and "http://" not in html


@pytest.mark.django_db
def test_read_only_navigation_hides_mutating_and_admin_actions(client, read_only_user):
    client.force_login(read_only_user)
    response = client.get(reverse("core:home"))
    html = response.content.decode()
    assert "Movements hub" not in html
    assert "Excel Import" not in html
    assert "Audit Log" not in html


@pytest.mark.django_db
def test_asset_filter_ui_uses_one_form_and_offers_clear_action(client, administrator):
    client.force_login(administrator)
    response = client.get(reverse("inventory:asset_list"), {"brand": "Fortinet"})
    html = response.content.decode()
    assert html.count('class="toolbar toolbar--filters"') == 1
    assert "Active filters" in html
    assert "Clear all" in html
