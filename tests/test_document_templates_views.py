import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.documents.models import DocumentTemplate, DocumentType
from apps.documents.template_services import publish_template, update_template

VALID_HTML = "<html><body><h1>{{ document_number }}</h1></body></html>"

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6360000002000155e75dd8000000004"
    "9454e44ae426082"
)

VALID_STYLE = {
    "logo_position": "left",
    "accent_color": "#336699",
    "font_choice": "serif",
    "page_margin": "compact",
    "section_spacing": "normal",
    "page_size": "A4",
    "orientation": "portrait",
}


@pytest.mark.django_db
class TestPermissions:
    def test_anonymous_redirected(self, client):
        assert client.get(reverse("documents:template_hub")).status_code == 302
        assert client.get(reverse("documents:template_edit", args=["delivery"])).status_code == 302

    def test_stock_manager_can_view_hub_but_not_edit(self, client, stock_manager):
        """Phase 9: a Stock Manager gets read-only preview access to
        whatever's currently live — the hub itself is now open to them —
        but editing/saving/resetting stays Administrator-only.
        """
        client.force_login(stock_manager)
        assert client.get(reverse("documents:template_hub")).status_code == 200
        assert client.get(reverse("documents:template_edit", args=["delivery"])).status_code == 403

    def test_read_only_forbidden(self, client, read_only_user):
        client.force_login(read_only_user)
        assert client.get(reverse("documents:template_hub")).status_code == 403


@pytest.mark.django_db
class TestHub:
    def test_administrator_can_view_hub(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("documents:template_hub"))
        assert response.status_code == 200
        assert "Assignment" in response.content.decode()
        assert "Delivery" in response.content.decode()

    def test_unknown_document_type_404s(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("documents:template_edit", args=["not-a-real-type"]))
        assert response.status_code == 404


@pytest.mark.django_db
class TestEditView:
    def test_get_shows_no_html_and_defaults_to_packaged_style(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("documents:template_edit", args=["delivery"]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "{{ document_number }}" not in content
        assert 'name="html_source"' not in content

    def test_saves_valid_style_choices(self, client, administrator):
        client.force_login(administrator)
        response = client.post(reverse("documents:template_edit", args=["delivery"]), VALID_STYLE)
        assert response.status_code == 302
        template_obj = DocumentTemplate.objects.get(document_type="delivery")
        assert template_obj.logo_position == "left"
        assert template_obj.accent_color == "#336699"
        assert template_obj.font_choice == "serif"
        assert template_obj.page_margin == "compact"
        assert "{{ document_number }}" in template_obj.html_source

    def test_rejects_an_invalid_accent_color_with_form_error(self, client, administrator):
        client.force_login(administrator)
        data = {**VALID_STYLE, "accent_color": "#zzzzzz"}
        response = client.post(reverse("documents:template_edit", args=["delivery"]), data)
        assert response.status_code == 200
        assert "#rrggbb" in response.content.decode()
        assert not DocumentTemplate.objects.filter(document_type="delivery").exists()

    def test_saves_with_a_logo_upload(self, client, administrator):
        client.force_login(administrator)
        png_bytes = bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
            "1f15c4890000000a49444154789c6360000002000155e75dd8000000004"
            "9454e44ae426082"
        )
        logo = SimpleUploadedFile("logo.png", png_bytes, content_type="image/png")
        response = client.post(
            reverse("documents:template_edit", args=["delivery"]),
            {**VALID_STYLE, "logo": logo},
        )
        assert response.status_code == 302
        assert DocumentTemplate.objects.get(document_type="delivery").logo

    def test_stock_manager_cannot_save(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.post(reverse("documents:template_edit", args=["delivery"]), VALID_STYLE)
        assert response.status_code == 403
        assert not DocumentTemplate.objects.filter(document_type="delivery").exists()


@pytest.mark.django_db
class TestPreviewView:
    def test_returns_a_real_pdf(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            reverse("documents:template_preview", args=["delivery"]), VALID_STYLE
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content[:4] == b"%PDF"

    def test_invalid_accent_color_returns_400_not_500(self, client, administrator):
        client.force_login(administrator)
        data = {**VALID_STYLE, "accent_color": "#zzzzzz"}
        response = client.post(reverse("documents:template_preview", args=["delivery"]), data)
        assert response.status_code == 400

    def test_stock_manager_forbidden(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.post(
            reverse("documents:template_preview", args=["delivery"]), VALID_STYLE
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestLivePreviewView:
    """apps.documents.views.DocumentTemplateLivePreviewView — renders
    whatever's currently live against sample data. Administrator and Stock
    Manager both get it (phase 9); Read-only does not.
    """

    def test_administrator_can_preview(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("documents:template_live_preview", args=["delivery"]))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content[:4] == b"%PDF"

    def test_stock_manager_can_preview(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.get(reverse("documents:template_live_preview", args=["delivery"]))
        assert response.status_code == 200
        assert response.content[:4] == b"%PDF"

    def test_read_only_forbidden(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("documents:template_live_preview", args=["delivery"]))
        assert response.status_code == 403

    def test_missing_logo_file_returns_friendly_error_not_500(self, client, administrator):
        """Regression test: a published template's logo unreadable from
        storage (deleted out from under the app, or a storage-permission
        problem) used to raise an uncaught OSError here — a raw 500 with no
        exception handling at all, unlike the equivalent real-generation
        path which already had a friendly-message fallback.
        """
        template_obj = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            logo=SimpleUploadedFile("logo.png", PNG_BYTES, content_type="image/png"),
        )
        publish_template(user=administrator, document_type=DocumentType.DELIVERY)
        # Simulate the file vanishing from storage without touching the DB
        # row — exactly what a permission problem or an out-of-band delete
        # looks like from Django's perspective.
        template_obj.logo.storage.delete(template_obj.logo.name)

        client.force_login(administrator)
        response = client.get(reverse("documents:template_live_preview", args=["delivery"]))
        assert response.status_code == 500
        assert b"storage permissions" in response.content

    def test_unknown_document_type_404s(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("documents:template_live_preview", args=["not-a-real-type"]))
        assert response.status_code == 404


@pytest.mark.django_db
class TestResetView:
    def test_resets_and_redirects(self, client, administrator):
        """Reset deactivates, never deletes (spec: "deactivated but not
        deleted") — the row still exists, just is_active=False, so
        get_template() (and this screen) treats the type as back to the
        packaged default.
        """
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        client.force_login(administrator)
        response = client.post(reverse("documents:template_reset", args=["delivery"]))
        assert response.status_code == 302
        template_obj = DocumentTemplate.objects.get(document_type="delivery")
        assert template_obj.is_active is False

    def test_stock_manager_cannot_reset(self, client, administrator, stock_manager):
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        client.force_login(stock_manager)
        response = client.post(reverse("documents:template_reset", args=["delivery"]))
        assert response.status_code == 403
        assert DocumentTemplate.objects.filter(document_type="delivery").exists()
