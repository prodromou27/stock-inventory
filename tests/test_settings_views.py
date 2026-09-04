import pytest
from django.urls import reverse

from apps.settings.models import SystemSettings

from .test_settings_services import VALID_CERT_PEM, VALID_KEY_PEM, _png_upload


@pytest.mark.django_db
class TestSettingsHubPermissions:
    def test_anonymous_redirected(self, client):
        assert client.get(reverse("settings:hub")).status_code == 302

    def test_stock_manager_can_view(self, client, stock_manager):
        # Settings now also hosts Locations, which Stock Managers may view
        # and edit within their assigned countries — the hub itself is not
        # an admin-only gate. Admin-only cards (Access & data, System &
        # documents) are hidden in the template, not blocked at this view.
        client.force_login(stock_manager)
        response = client.get(reverse("settings:hub"))
        assert response.status_code == 200
        assert b"Access & data" not in response.content

    def test_read_only_can_view(self, client, read_only_user):
        # Read-only users cannot modify locations but must still be able to
        # reach the location hierarchy, which now lives under Settings.
        client.force_login(read_only_user)
        response = client.get(reverse("settings:hub"))
        assert response.status_code == 200
        assert b"Access & data" not in response.content

    def test_administrator_can_view(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("settings:hub"))
        assert response.status_code == 200
        assert b"Access & data" in response.content


@pytest.mark.django_db
class TestSystemConfigurationView:
    def test_administrator_can_view(self, client, administrator):
        client.force_login(administrator)
        assert client.get(reverse("settings:system")).status_code == 200

    def test_stock_manager_forbidden(self, client, stock_manager):
        client.force_login(stock_manager)
        assert client.get(reverse("settings:system")).status_code == 403

    def test_saves_valid_settings(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            reverse("settings:system"),
            {"site_name": "Acme Inventory", "allowed_hosts_override": "acme.example.com"},
        )
        assert response.status_code == 302

        settings_obj = SystemSettings.load()
        assert settings_obj.site_name == "Acme Inventory"
        assert settings_obj.allowed_hosts_override == "acme.example.com"

    def test_saved_site_name_appears_in_the_sidebar(self, client, administrator):
        client.force_login(administrator)
        client.post(
            reverse("settings:system"),
            {"site_name": "Acme Inventory", "allowed_hosts_override": ""},
        )
        response = client.get(reverse("core:home"))
        assert "Acme Inventory" in response.content.decode()

    def test_rejects_a_non_image_logo(self, client, administrator):
        from django.core.files.uploadedfile import SimpleUploadedFile

        client.force_login(administrator)
        response = client.post(
            reverse("settings:system"),
            {
                "site_name": "Acme",
                "allowed_hosts_override": "",
                "logo": SimpleUploadedFile("logo.txt", b"not an image", content_type="text/plain"),
            },
        )
        assert response.status_code == 200
        assert "PNG or JPEG" in response.content.decode()

    def test_saves_a_valid_logo(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            reverse("settings:system"),
            {"site_name": "Acme", "allowed_hosts_override": "", "logo": _png_upload()},
        )
        assert response.status_code == 302
        assert SystemSettings.load().logo


@pytest.mark.django_db
class TestCertificateUploadView:
    def test_administrator_can_view(self, client, administrator):
        client.force_login(administrator)
        assert client.get(reverse("settings:certificates")).status_code == 200

    def test_stock_manager_forbidden(self, client, stock_manager):
        client.force_login(stock_manager)
        assert client.get(reverse("settings:certificates")).status_code == 403

    def test_uploads_a_valid_pair(self, client, administrator, certs_dir):
        import os

        from django.core.files.uploadedfile import SimpleUploadedFile

        client.force_login(administrator)
        response = client.post(
            reverse("settings:certificates"),
            {
                "cert_file": SimpleUploadedFile("fullchain.pem", VALID_CERT_PEM),
                "key_file": SimpleUploadedFile("privkey.pem", VALID_KEY_PEM),
            },
            follow=True,
        )
        assert response.status_code == 200
        assert "restart proxy" in response.content.decode()
        assert os.path.exists(os.path.join(certs_dir, "fullchain.pem"))

    def test_rejects_a_mismatched_pair_with_form_error(self, client, administrator, certs_dir):
        from django.core.files.uploadedfile import SimpleUploadedFile

        client.force_login(administrator)
        response = client.post(
            reverse("settings:certificates"),
            {
                "cert_file": SimpleUploadedFile("fullchain.pem", VALID_CERT_PEM),
                "key_file": SimpleUploadedFile("privkey.pem", b"not a real key"),
            },
        )
        assert response.status_code == 200
        assert "not a valid, matching pair" in response.content.decode()
