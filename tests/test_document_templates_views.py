import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.documents.models import DocumentTemplate, DocumentType
from apps.documents.template_services import update_template

VALID_HTML = "<html><body><h1>{{ document_number }}</h1></body></html>"

VALID_STYLE = {
    "logo_position": "left",
    "accent_color": "#336699",
    "font_choice": "serif",
    "page_margin": "compact",
}


@pytest.mark.django_db
class TestPermissions:
    def test_anonymous_redirected(self, client):
        assert client.get(reverse("documents:template_hub")).status_code == 302
        assert client.get(reverse("documents:template_edit", args=["delivery"])).status_code == 302

    def test_stock_manager_forbidden(self, client, stock_manager):
        client.force_login(stock_manager)
        assert client.get(reverse("documents:template_hub")).status_code == 403
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
        assert "<textarea" not in content

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
class TestResetView:
    def test_resets_and_redirects(self, client, administrator):
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        client.force_login(administrator)
        response = client.post(reverse("documents:template_reset", args=["delivery"]))
        assert response.status_code == 302
        assert not DocumentTemplate.objects.filter(document_type="delivery").exists()

    def test_stock_manager_cannot_reset(self, client, administrator, stock_manager):
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        client.force_login(stock_manager)
        response = client.post(reverse("documents:template_reset", args=["delivery"]))
        assert response.status_code == 403
        assert DocumentTemplate.objects.filter(document_type="delivery").exists()
