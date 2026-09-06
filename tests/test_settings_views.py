import pytest
from django.urls import reverse

from apps.settings.models import SystemSettings
from apps.settings.services import MAX_CERTIFICATE_FILE_SIZE_BYTES

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

    def test_administrator_sees_timezone_and_smtp_cards(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("settings:hub"))
        content = response.content.decode()
        assert "Timezone" in content
        assert "Email (SMTP)" in content

    def test_stock_manager_does_not_see_timezone_and_smtp_cards(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.get(reverse("settings:hub"))
        content = response.content.decode()
        assert reverse("settings:timezone") not in content
        assert reverse("settings:smtp") not in content


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

    def test_saves_a_valid_accent_color(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            reverse("settings:system"),
            {
                "site_name": "Acme",
                "allowed_hosts_override": "",
                "accent_color": "#2563eb",
            },
        )
        assert response.status_code == 302
        assert SystemSettings.load().accent_color == "#2563eb"

    def test_rejects_an_invalid_accent_color(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            reverse("settings:system"),
            {"site_name": "Acme", "allowed_hosts_override": "", "accent_color": "blue"},
        )
        assert response.status_code == 200
        assert "#rrggbb" in response.content.decode()


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

    def test_write_failure_shows_a_safe_message_not_the_raw_path(
        self, client, administrator, certs_dir, monkeypatch
    ):
        """Regression test: an OSError while writing the certificate files
        used to be shown to the Administrator verbatim, including the
        server's absolute CERTS_DIR filesystem path — a deployment detail,
        not something typed into this form. Full detail now goes to the
        logs only; the form gets a safe, generic message.
        """
        import os

        from django.core.files.uploadedfile import SimpleUploadedFile

        def failing_chmod(*args, **kwargs):
            raise OSError(f"simulated failure writing to {certs_dir}")

        monkeypatch.setattr(os, "chmod", failing_chmod)

        client.force_login(administrator)
        response = client.post(
            reverse("settings:certificates"),
            {
                "cert_file": SimpleUploadedFile("fullchain.pem", VALID_CERT_PEM),
                "key_file": SimpleUploadedFile("privkey.pem", VALID_KEY_PEM),
            },
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert certs_dir not in content
        assert "check the server logs" in content

    def test_rejects_oversized_certificate_before_reading_it(
        self, client, administrator, certs_dir
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        client.force_login(administrator)
        response = client.post(
            reverse("settings:certificates"),
            {
                "cert_file": SimpleUploadedFile(
                    "fullchain.pem", b"x" * (MAX_CERTIFICATE_FILE_SIZE_BYTES + 1)
                ),
                "key_file": SimpleUploadedFile("privkey.pem", VALID_KEY_PEM),
            },
        )

        assert response.status_code == 200
        assert "5 MB or smaller" in response.content.decode()


@pytest.mark.django_db
class TestTimezoneConfigurationView:
    def test_administrator_can_view(self, client, administrator):
        client.force_login(administrator)
        assert client.get(reverse("settings:timezone")).status_code == 200

    def test_stock_manager_forbidden(self, client, stock_manager):
        client.force_login(stock_manager)
        assert client.get(reverse("settings:timezone")).status_code == 403

    def test_saves_a_valid_timezone(self, client, administrator):
        client.force_login(administrator)
        response = client.post(reverse("settings:timezone"), {"timezone": "America/New_York"})
        assert response.status_code == 302
        assert SystemSettings.load().timezone == "America/New_York"

    def test_rejects_an_unrecognized_timezone(self, client, administrator):
        client.force_login(administrator)
        response = client.post(reverse("settings:timezone"), {"timezone": "Not/AZone"})
        assert response.status_code == 200


@pytest.mark.django_db
class TestSmtpConfigurationView:
    def test_administrator_can_view(self, client, administrator):
        client.force_login(administrator)
        assert client.get(reverse("settings:smtp")).status_code == 200

    def test_stock_manager_forbidden(self, client, stock_manager):
        client.force_login(stock_manager)
        assert client.get(reverse("settings:smtp")).status_code == 403

    def test_saves_smtp_settings(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            reverse("settings:smtp"),
            {
                "smtp_host": "smtp.example.com",
                "smtp_port": "587",
                "smtp_username": "bot@example.com",
                "smtp_password": "hunter2",
                "smtp_use_tls": "on",
                "smtp_from_email": "noreply@example.com",
            },
            follow=True,
        )
        assert response.status_code == 200
        settings_obj = SystemSettings.load()
        assert settings_obj.smtp_host == "smtp.example.com"
        assert "SMTP settings saved" in response.content.decode()

    def test_get_never_prefills_the_saved_password(self, client, administrator):
        """Regression test: the SMTP password used to round-trip back into
        this field's HTML in cleartext on every GET (render_value=True) —
        sitting in browser cache/view-source/screenshots. It must render
        blank regardless of what's saved.
        """
        client.force_login(administrator)
        client.post(
            reverse("settings:smtp"),
            {
                "smtp_host": "smtp.example.com",
                "smtp_port": "587",
                "smtp_username": "bot@example.com",
                "smtp_password": "super-secret-password",
                "smtp_use_tls": "on",
                "smtp_from_email": "noreply@example.com",
            },
        )
        response = client.get(reverse("settings:smtp"))
        assert "super-secret-password" not in response.content.decode()
        assert response.context["form"]["smtp_password"].value() in (None, "")

    def test_blank_password_on_save_keeps_the_existing_one(self, client, administrator):
        """Since the password is never pre-filled, a save that isn't
        touching it would otherwise submit a blank value and wipe it."""
        client.force_login(administrator)
        client.post(
            reverse("settings:smtp"),
            {
                "smtp_host": "smtp.example.com",
                "smtp_port": "587",
                "smtp_username": "bot@example.com",
                "smtp_password": "keep-me",
                "smtp_use_tls": "on",
                "smtp_from_email": "noreply@example.com",
            },
        )
        client.post(
            reverse("settings:smtp"),
            {
                "smtp_host": "smtp.example.com",
                "smtp_port": "587",
                "smtp_username": "bot@example.com",
                "smtp_password": "",
                "smtp_use_tls": "on",
                "smtp_from_email": "changed-from@example.com",
            },
        )
        settings_obj = SystemSettings.load()
        assert settings_obj.smtp_password == "keep-me"
        assert settings_obj.smtp_from_email == "changed-from@example.com"

    def test_saving_with_a_test_recipient_sends_and_reports_success(
        self, client, administrator, settings, mailoutbox
    ):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        client.force_login(administrator)
        response = client.post(
            reverse("settings:smtp"),
            {
                "smtp_host": "smtp.example.com",
                "smtp_port": "587",
                "smtp_username": "bot@example.com",
                "smtp_password": "hunter2",
                "smtp_use_tls": "on",
                "smtp_from_email": "noreply@example.com",
                "test_email_recipient": "ops@example.com",
            },
            follow=True,
        )
        assert response.status_code == 200
        assert "Test email sent" in response.content.decode()
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["ops@example.com"]
