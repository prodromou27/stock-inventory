import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.documents.models import DocumentTemplate, DocumentType
from apps.documents.template_services import publish_template, submit_for_review, update_template

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

# VALID_STYLE alone leaves template_completeness() unsatisfied (no title/
# company, no preview confirmation, and show_signature_block — a checkbox
# absent from POST data means unchecked regardless of the field's initial=
# — would submit as False) — this is what a POST needs to clear the
# checklist so submit_for_review()/publish_template() actually succeed.
PUBLISHABLE_STYLE = {
    **VALID_STYLE,
    "document_title": "Test document",
    "company_name": "Acme Corp",
    "logo_intentionally_omitted": "on",
    "preview_confirmed": "on",
    "show_signature_block": "on",
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

    def test_country_branding_link_hidden_from_stock_manager(self, client, stock_manager):
        """Regression test: the "Country branding" button was shown
        unconditionally (unlike the per-row "Edit" link, correctly gated
        behind {% if can_edit %}) — a Stock Manager legitimately on this
        hub to preview templates saw an actionable button that 403'd on
        click, since CountryBrandingListView is Administrator-only.
        """
        client.force_login(stock_manager)
        response = client.get(reverse("documents:template_hub"))
        assert response.status_code == 200
        assert "Country branding" not in response.content.decode()

    def test_country_branding_link_shown_to_administrator(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("documents:template_hub"))
        assert "Country branding" in response.content.decode()

    def test_pending_review_status_gets_its_own_badge_not_lumped_with_draft(
        self, client, administrator, second_administrator
    ):
        """Regression test: the hub's status column only distinguished
        Published from everything else — a template Pending review looked
        identical to a plain Draft, so the second Administrator who needs
        to approve it had no way to notice from this list view.
        """
        from apps.documents.template_services import submit_for_review, update_template

        update_template(
            user=administrator,
            document_type="delivery",
            html_source="<html><body><h1>{{ document_number }}</h1></body></html>",
            document_title="Delivery form",
            company_name="Acme Corp",
            logo_intentionally_omitted=True,
            preview_confirmed=True,
        )
        submit_for_review(user=administrator, document_type="delivery")

        client.force_login(second_administrator)
        response = client.get(reverse("documents:template_hub"))
        content = response.content.decode()
        assert "Pending review" in content
        assert "Custom — Draft" not in content


@pytest.mark.django_db
class TestEditView:
    def test_get_defaults_to_packaged_style_with_the_raw_html_section_collapsed(
        self, client, administrator
    ):
        """The structured fields (logo, colors, fonts, ...) are the default,
        prominent editing surface — raw template syntax only appears inside
        the opt-in "Raw HTML/CSS" <details>, which starts collapsed unless a
        template already has custom_html_enabled=True (none does here, this
        is a fresh document type). There's no field literally named
        html_source; TestCustomHtmlEditingView covers custom_html_source.
        """
        client.force_login(administrator)
        response = client.get(reverse("documents:template_edit", args=["delivery"]))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="html_source"' not in content
        assert "Raw HTML/CSS" in content
        # Neither <details> block (advanced layout, raw HTML) starts open
        # for a fresh template with no saved custom_html_enabled=True.
        assert 'template-editor__advanced" open' not in content

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

    def test_shows_completeness_checklist_after_saving(self, client, administrator):
        client.force_login(administrator)
        client.post(reverse("documents:template_edit", args=["delivery"]), VALID_STYLE)
        response = client.get(reverse("documents:template_edit", args=["delivery"]))
        keys = {item["key"] for item in response.context["completeness"]}
        assert keys == {"logo", "title", "company", "columns", "signatures", "preview"}
        unsatisfied = {
            item["key"] for item in response.context["completeness"] if not item["satisfied"]
        }
        assert "title" in unsatisfied  # VALID_STYLE never sets one

    def test_submit_for_review_blocked_shows_a_helpful_error(self, client, administrator):
        client.force_login(administrator)
        client.post(reverse("documents:template_edit", args=["delivery"]), VALID_STYLE)
        response = client.post(
            reverse("documents:template_submit_for_review", args=["delivery"]), follow=True
        )
        assert b"incomplete" in response.content
        assert DocumentTemplate.objects.get(document_type="delivery").status == "draft"

    def test_publish_blocked_on_a_plain_draft(self, client, administrator):
        client.force_login(administrator)
        client.post(reverse("documents:template_edit", args=["delivery"]), VALID_STYLE)
        response = client.post(
            reverse("documents:template_publish", args=["delivery"]), follow=True
        )
        assert b"Submit this template for review" in response.content
        assert DocumentTemplate.objects.get(document_type="delivery").status == "draft"

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

    def test_shows_starter_gallery_only_when_no_template_exists(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("documents:template_edit", args=["delivery"]))
        assert b"Start from a layout" in response.content

        client.post(reverse("documents:template_edit", args=["delivery"]), VALID_STYLE)
        response = client.get(reverse("documents:template_edit", args=["delivery"]))
        assert b"Start from a layout" not in response.content


@pytest.mark.django_db
class TestCustomHtmlEditingView:
    def test_toggle_on_saves_the_typed_html_verbatim_not_the_composed_skeleton(
        self, client, administrator
    ):
        client.force_login(administrator)
        custom_html = "<html><body><h1>Hand-typed {{ document_number }}</h1></body></html>"
        response = client.post(
            reverse("documents:template_edit", args=["delivery"]),
            {
                **VALID_STYLE,
                "custom_html_enabled": "on",
                "custom_html_source": custom_html,
            },
        )
        assert response.status_code == 302
        template_obj = DocumentTemplate.objects.get(document_type="delivery")
        assert template_obj.html_source == custom_html
        assert template_obj.custom_html_enabled is True

    def test_get_shows_the_section_open_once_already_enabled(self, client, administrator):
        client.force_login(administrator)
        client.post(
            reverse("documents:template_edit", args=["delivery"]),
            {
                **VALID_STYLE,
                "custom_html_enabled": "on",
                "custom_html_source": "<html><body>Custom</body></html>",
            },
        )
        response = client.get(reverse("documents:template_edit", args=["delivery"]))
        assert 'template-editor__advanced" open' in response.content.decode()

    def test_enabling_without_source_shows_a_form_error(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            reverse("documents:template_edit", args=["delivery"]),
            {**VALID_STYLE, "custom_html_enabled": "on", "custom_html_source": ""},
        )
        assert response.status_code == 200
        assert b"Enter the template HTML" in response.content
        assert not DocumentTemplate.objects.filter(document_type="delivery").exists()

    def test_turning_it_back_off_recomposes_from_the_structured_fields(self, client, administrator):
        client.force_login(administrator)
        client.post(
            reverse("documents:template_edit", args=["delivery"]),
            {
                **VALID_STYLE,
                "custom_html_enabled": "on",
                "custom_html_source": "<html><body>Custom</body></html>",
            },
        )
        client.post(
            reverse("documents:template_edit", args=["delivery"]),
            {**VALID_STYLE, "custom_html_enabled": "", "custom_html_source": ""},
        )
        template_obj = DocumentTemplate.objects.get(document_type="delivery")
        assert template_obj.custom_html_enabled is False
        assert "{{ document_number }}" in template_obj.html_source

    def test_get_prefills_the_current_source_for_a_fresh_template(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("documents:template_edit", args=["delivery"]))
        assert "{{ document_number }}" in response.context["form"].initial["custom_html_source"]

    def test_stock_manager_cannot_enable_custom_html(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.post(
            reverse("documents:template_edit", args=["delivery"]),
            {**VALID_STYLE, "custom_html_enabled": "on", "custom_html_source": "<html></html>"},
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestApplyStarterView:
    def test_creates_a_draft_and_redirects(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            reverse("documents:template_apply_starter", args=["delivery"]),
            {"preset_key": "minimal"},
        )
        assert response.status_code == 302
        template_obj = DocumentTemplate.objects.get(document_type="delivery")
        assert template_obj.font_choice == "mono"

    def test_unknown_preset_shows_an_error(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            reverse("documents:template_apply_starter", args=["delivery"]),
            {"preset_key": "does-not-exist"},
            follow=True,
        )
        assert b"Unknown starter template" in response.content
        assert not DocumentTemplate.objects.filter(document_type="delivery").exists()

    def test_stock_manager_forbidden(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.post(
            reverse("documents:template_apply_starter", args=["delivery"]),
            {"preset_key": "classic"},
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestApprovalWorkflowViews:
    def test_submit_then_a_different_administrator_publishes(
        self, client, administrator, second_administrator
    ):
        client.force_login(administrator)
        client.post(reverse("documents:template_edit", args=["delivery"]), PUBLISHABLE_STYLE)
        response = client.post(reverse("documents:template_submit_for_review", args=["delivery"]))
        assert response.status_code == 302
        assert DocumentTemplate.objects.get(document_type="delivery").status == "pending_review"

        client.force_login(second_administrator)
        response = client.post(reverse("documents:template_publish", args=["delivery"]))
        assert response.status_code == 302
        template_obj = DocumentTemplate.objects.get(document_type="delivery")
        assert template_obj.status == "published"
        assert template_obj.approved_by == second_administrator

    def test_submitter_cannot_publish_their_own_submission(self, client, administrator):
        client.force_login(administrator)
        client.post(reverse("documents:template_edit", args=["delivery"]), PUBLISHABLE_STYLE)
        client.post(reverse("documents:template_submit_for_review", args=["delivery"]))

        response = client.post(
            reverse("documents:template_publish", args=["delivery"]), follow=True
        )
        assert b"different Administrator" in response.content
        assert DocumentTemplate.objects.get(document_type="delivery").status == "pending_review"

    def test_reject_sends_it_back_to_draft(self, client, administrator, second_administrator):
        client.force_login(administrator)
        client.post(reverse("documents:template_edit", args=["delivery"]), PUBLISHABLE_STYLE)
        client.post(reverse("documents:template_submit_for_review", args=["delivery"]))

        client.force_login(second_administrator)
        response = client.post(reverse("documents:template_reject_review", args=["delivery"]))
        assert response.status_code == 302
        assert DocumentTemplate.objects.get(document_type="delivery").status == "draft"

    def test_stock_manager_cannot_submit_or_reject(self, client, stock_manager, administrator):
        client.force_login(administrator)
        client.post(reverse("documents:template_edit", args=["delivery"]), PUBLISHABLE_STYLE)

        client.force_login(stock_manager)
        assert (
            client.post(
                reverse("documents:template_submit_for_review", args=["delivery"])
            ).status_code
            == 403
        )
        assert (
            client.post(reverse("documents:template_reject_review", args=["delivery"])).status_code
            == 403
        )


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

    def test_missing_logo_file_returns_friendly_error_not_500(
        self, client, administrator, second_administrator
    ):
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
            document_title="Delivery form",
            company_name="Acme Corp",
            preview_confirmed=True,
        )
        submit_for_review(user=administrator, document_type=DocumentType.DELIVERY)
        publish_template(user=second_administrator, document_type=DocumentType.DELIVERY)
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
