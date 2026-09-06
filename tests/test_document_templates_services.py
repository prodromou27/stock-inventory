from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.audit.models import AuditEvent
from apps.documents.models import (
    DocumentTemplate,
    DocumentTemplateVersion,
    DocumentType,
    FontChoice,
    PageMargin,
)
from apps.documents.pdf import default_template_source, layout_context, render_styleable_source
from apps.documents.services import generate_document
from apps.documents.template_services import (
    duplicate_template,
    get_template,
    publish_template,
    render_preview_pdf,
    reset_template,
    restore_template_version,
    submit_for_review,
    update_template,
)
from apps.inventory.models import UnitAsset
from apps.inventory.services.assignments import deliver_to_customer
from apps.inventory.services.receipts import receive_stock

VALID_HTML = "<html><body><h1>{{ document_number }}</h1><p>{{ final_customer }}</p></body></html>"
BROKEN_HTML = "{% for x in %}broken"

# publish_template() gates on template_completeness() — every call site
# that publishes needs the underlying update_template() call to satisfy it
# (title, company details, logo-or-explicitly-omitted, and a fresh preview
# confirmation; signatures/columns are satisfied by the model's own
# defaults). Centralized here so a checklist change only needs updating once.
PUBLISH_READY_KWARGS = {
    "document_title": "Test document",
    "company_name": "Acme Corp",
    "logo_intentionally_omitted": True,
    "preview_confirmed": True,
}


def _submit_and_publish(*, document_type, submitter, approver):
    """publish_template() requires a *different* Administrator from
    whoever submitted it — a plain single-admin publish_template() call no
    longer takes a Draft straight to Published on its own.
    """
    submit_for_review(user=submitter, document_type=document_type)
    return publish_template(user=approver, document_type=document_type)


PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6360000002000155e75dd8000000004"
    "9454e44ae426082"
)


def _png_upload(name="logo.png"):
    return SimpleUploadedFile(name, PNG_BYTES, content_type="image/png")


@pytest.fixture
def delivery_txn(administrator, unit_product, location_tree):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="SN-TPL-DELIVER",
    )
    asset = UnitAsset.objects.get(vendor_serial="SN-TPL-DELIVER")
    return deliver_to_customer(
        user=administrator,
        final_customer="Template Test Corp",
        occurred_at=date.today(),
        unit_asset_ids=[asset.pk],
    )


@pytest.mark.django_db
class TestDefaultTemplateSource:
    def test_reads_the_packaged_template(self):
        source = default_template_source()
        assert "{{ document_number }}" in source
        assert "<html" in source.lower()


@pytest.mark.django_db
class TestUpdateTemplate:
    def test_saves_a_valid_template(self, administrator):
        template_obj = update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        assert template_obj.html_source == VALID_HTML
        assert template_obj.updated_by == administrator
        assert DocumentTemplate.objects.count() == 1

    def test_updating_again_reuses_the_same_row(self, administrator):
        first = update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        second = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML + "<p>v2</p>",
        )
        assert first.pk == second.pk
        assert DocumentTemplate.objects.count() == 1

    def test_rejects_a_broken_template_without_saving(self, administrator):
        with pytest.raises(ValidationError, match="failed to render"):
            update_template(
                user=administrator, document_type=DocumentType.DELIVERY, html_source=BROKEN_HTML
            )
        assert get_template(DocumentType.DELIVERY) is None

    def test_broken_update_does_not_overwrite_a_good_saved_template(self, administrator):
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        with pytest.raises(ValidationError):
            update_template(
                user=administrator, document_type=DocumentType.DELIVERY, html_source=BROKEN_HTML
            )
        assert get_template(DocumentType.DELIVERY).html_source == VALID_HTML

    def test_saves_a_logo(self, administrator):
        template_obj = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            logo=_png_upload(),
        )
        assert template_obj.logo.name

    def test_rejects_a_non_image_logo(self, administrator):
        bad_file = SimpleUploadedFile("logo.txt", b"not an image", content_type="text/plain")
        with pytest.raises(ValidationError, match="PNG or JPEG"):
            update_template(
                user=administrator,
                document_type=DocumentType.DELIVERY,
                html_source=VALID_HTML,
                logo=bad_file,
            )

    def test_remove_logo_clears_it(self, administrator):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            logo=_png_upload(),
        )
        updated = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            remove_logo=True,
        )
        assert not updated.logo

    def test_replacing_a_logo_removes_the_old_file(self, administrator):
        template_obj = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            logo=_png_upload("old.png"),
        )
        old_name = template_obj.logo.name

        updated = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            logo=_png_upload("new.png"),
        )

        assert updated.logo.name != old_name
        assert not updated.logo.storage.exists(old_name)

    def test_requires_administrator(self, stock_manager):
        with pytest.raises(PermissionDenied):
            update_template(
                user=stock_manager, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
            )

    def test_records_audit_event(self, administrator):
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.RECORD_CREATED, object_type="DocumentTemplate"
        ).exists()


@pytest.mark.django_db
class TestRenderStyleableSource:
    """apps.documents.pdf.render_styleable_source() — what the structured,
    no-HTML editor (apps.documents.views.DocumentTemplateEditView) uses to
    compose html_source; the data fields it produces must always match what
    the packaged form_v1.html already exposes (default_template_source()),
    since neither is ever hand-edited.
    """

    def test_includes_the_same_data_fields_as_the_packaged_default(self):
        source = render_styleable_source(
            logo_position="left",
            accent_color="#123456",
            font_choice=FontChoice.SANS,
            page_margin=PageMargin.NORMAL,
        )
        for token in (
            "{{ document_number }}",
            "{{ transaction_number }}",
            "{% for line in lines %}",
        ):
            assert token in source
            assert token in default_template_source()

    def test_applies_the_chosen_style_values(self):
        source = render_styleable_source(
            logo_position="right",
            accent_color="#123456",
            font_choice=FontChoice.SERIF,
            page_margin=PageMargin.SPACIOUS,
        )
        assert "#123456" in source
        assert "letterhead--right" in source
        assert "Liberation Serif" in source
        assert "margin: 2.5cm" in source

    def test_renders_as_a_real_pdf(self):
        source = render_styleable_source(
            logo_position="center",
            accent_color="#000000",
            font_choice=FontChoice.MONO,
            page_margin=PageMargin.COMPACT,
        )
        pdf_bytes = render_preview_pdf(document_type=DocumentType.DELIVERY, html_source=source)
        assert pdf_bytes[:4] == b"%PDF"


@pytest.mark.django_db
class TestUpdateTemplateStyleFields:
    def test_saves_the_structured_style_fields(self, administrator):
        template_obj = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            logo_position="right",
            accent_color="#abcdef",
            font_choice=FontChoice.SERIF,
            page_margin=PageMargin.SPACIOUS,
        )
        assert template_obj.logo_position == "right"
        assert template_obj.accent_color == "#abcdef"
        assert template_obj.font_choice == FontChoice.SERIF
        assert template_obj.page_margin == PageMargin.SPACIOUS

    def test_omitted_style_fields_keep_model_defaults(self, administrator):
        template_obj = update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        assert template_obj.logo_position == "left"
        assert template_obj.accent_color == "#444444"
        assert template_obj.font_choice == FontChoice.SANS

    def test_preview_confirmed_resets_on_every_subsequent_save(self, administrator):
        template_obj = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            preview_confirmed=True,
        )
        assert template_obj.preview_confirmed is True

        template_obj = update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        assert template_obj.preview_confirmed is False


@pytest.mark.django_db
class TestTemplateCompletenessAndPublishGate:
    """apps.documents.template_services.template_completeness()/
    publish_template() — the pre-publish checklist (spec: "logo, title,
    company details, required sections, signatures, and PDF preview
    confirmation").
    """

    def test_brand_new_draft_is_incomplete(self, administrator):
        from apps.documents.template_services import template_completeness

        template_obj = update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        items = template_completeness(template_obj)
        unsatisfied = {item["key"] for item in items if not item["satisfied"]}
        assert unsatisfied == {"logo", "title", "company", "preview"}

    def test_submit_for_review_blocked_until_checklist_satisfied(self, administrator):
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        with pytest.raises(ValidationError, match="incomplete"):
            submit_for_review(user=administrator, document_type=DocumentType.DELIVERY)
        assert get_template(DocumentType.DELIVERY).status == "draft"

    def test_publish_succeeds_once_every_item_is_satisfied(
        self, administrator, second_administrator
    ):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            **PUBLISH_READY_KWARGS,
        )
        published = _submit_and_publish(
            document_type=DocumentType.DELIVERY,
            submitter=administrator,
            approver=second_administrator,
        )
        assert published.status == "published"

    def test_logo_file_satisfies_the_logo_item_without_the_checkbox(self, administrator):
        from apps.documents.template_services import template_completeness

        template_obj = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            logo=_png_upload(),
        )
        items = {item["key"]: item["satisfied"] for item in template_completeness(template_obj)}
        assert items["logo"] is True

    def test_hiding_every_column_fails_the_columns_item(self, administrator):
        from apps.documents.models import REPORT_COLUMNS
        from apps.documents.template_services import template_completeness

        template_obj = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            layout_config={"hidden_columns": [key for key, _ in REPORT_COLUMNS]},
        )
        items = {item["key"]: item["satisfied"] for item in template_completeness(template_obj)}
        assert items["columns"] is False

    def test_hiding_the_signature_block_fails_the_signatures_item(self, administrator):
        from apps.documents.template_services import template_completeness

        template_obj = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            layout_config={"show_signature_block": False},
        )
        items = {item["key"]: item["satisfied"] for item in template_completeness(template_obj)}
        assert items["signatures"] is False

    def test_no_template_yet_has_an_empty_checklist(self):
        from apps.documents.template_services import template_completeness

        assert template_completeness(None) == []


@pytest.mark.django_db
class TestApprovalWorkflow:
    """apps.documents.template_services.submit_for_review()/publish_template()/
    reject_review() — the two-Administrator publish workflow (spec:
    "Administrator drafts; a second authorized Administrator reviews and
    publishes").
    """

    def test_submit_moves_draft_to_pending_review(self, administrator):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            **PUBLISH_READY_KWARGS,
        )
        submitted = submit_for_review(user=administrator, document_type=DocumentType.DELIVERY)
        assert submitted.status == "pending_review"
        assert submitted.submitted_by == administrator
        assert submitted.submitted_at is not None

    def test_publish_rejects_the_same_submitter_as_approver(self, administrator):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            **PUBLISH_READY_KWARGS,
        )
        submit_for_review(user=administrator, document_type=DocumentType.DELIVERY)
        with pytest.raises(ValidationError, match="different Administrator"):
            publish_template(user=administrator, document_type=DocumentType.DELIVERY)
        assert get_template(DocumentType.DELIVERY).status == "pending_review"

    def test_a_different_administrator_can_publish(self, administrator, second_administrator):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            **PUBLISH_READY_KWARGS,
        )
        submit_for_review(user=administrator, document_type=DocumentType.DELIVERY)
        published = publish_template(user=second_administrator, document_type=DocumentType.DELIVERY)
        assert published.status == "published"
        assert published.approved_by == second_administrator
        assert published.approved_at is not None

    def test_publish_blocked_while_still_a_plain_draft(self, administrator):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            **PUBLISH_READY_KWARGS,
        )
        with pytest.raises(ValidationError, match="Submit this template for review"):
            publish_template(user=administrator, document_type=DocumentType.DELIVERY)

    def test_editing_a_pending_review_template_reverts_it_to_draft(self, administrator):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            **PUBLISH_READY_KWARGS,
        )
        submit_for_review(user=administrator, document_type=DocumentType.DELIVERY)

        edited = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            **PUBLISH_READY_KWARGS,
        )
        assert edited.status == "draft"
        assert edited.submitted_by is None
        assert edited.submitted_at is None

    def test_reject_sends_it_back_to_draft(self, administrator, second_administrator):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            **PUBLISH_READY_KWARGS,
        )
        submit_for_review(user=administrator, document_type=DocumentType.DELIVERY)

        from apps.documents.template_services import reject_review

        rejected = reject_review(
            user=second_administrator,
            document_type=DocumentType.DELIVERY,
            reason="wrong terms wording",
        )
        assert rejected.status == "draft"
        assert rejected.submitted_by is None

    def test_reject_requires_pending_review_status(self, administrator):
        from apps.documents.template_services import reject_review

        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        with pytest.raises(ValidationError, match="not currently pending review"):
            reject_review(user=administrator, document_type=DocumentType.DELIVERY)

    def test_publish_is_a_noop_once_already_published(self, administrator, second_administrator):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            **PUBLISH_READY_KWARGS,
        )
        published = _submit_and_publish(
            document_type=DocumentType.DELIVERY,
            submitter=administrator,
            approver=second_administrator,
        )
        again = publish_template(user=administrator, document_type=DocumentType.DELIVERY)
        assert again.pk == published.pk
        assert again.status == "published"

    def test_requires_administrator(self, stock_manager, administrator):
        with pytest.raises(PermissionDenied):
            submit_for_review(user=stock_manager, document_type=DocumentType.DELIVERY)
        with pytest.raises(PermissionDenied):
            publish_template(user=stock_manager, document_type=DocumentType.DELIVERY)


@pytest.mark.django_db
class TestResetTemplate:
    def test_reverts_to_packaged_default(self, administrator):
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        reset_template(user=administrator, document_type=DocumentType.DELIVERY)
        assert get_template(DocumentType.DELIVERY) is None

    def test_noop_when_nothing_to_reset(self, administrator):
        reset_template(user=administrator, document_type=DocumentType.DELIVERY)  # should not raise
        assert get_template(DocumentType.DELIVERY) is None

    def test_requires_administrator(self, administrator, stock_manager):
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        with pytest.raises(PermissionDenied):
            reset_template(user=stock_manager, document_type=DocumentType.DELIVERY)


@pytest.mark.django_db
class TestApplyStarterTemplate:
    def test_creates_a_draft_from_a_known_preset(self, administrator):
        from apps.documents.template_services import apply_starter_template

        template_obj = apply_starter_template(
            user=administrator, document_type=DocumentType.DELIVERY, preset_key="classic"
        )
        assert template_obj.status == "draft"
        assert template_obj.accent_color == "#1d4ed8"
        assert template_obj.logo_position == "left"
        assert template_obj.font_choice == "serif"
        assert "{{ document_number }}" in template_obj.html_source

    def test_unknown_preset_is_rejected(self, administrator):
        from apps.documents.template_services import apply_starter_template

        with pytest.raises(ValidationError, match="Unknown starter template"):
            apply_starter_template(
                user=administrator, document_type=DocumentType.DELIVERY, preset_key="not-a-preset"
            )
        assert get_template(DocumentType.DELIVERY) is None

    def test_rejects_when_the_type_already_has_a_template(self, administrator):
        from apps.documents.template_services import apply_starter_template

        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        with pytest.raises(ValidationError, match="already has a template"):
            apply_starter_template(
                user=administrator, document_type=DocumentType.DELIVERY, preset_key="classic"
            )

    def test_requires_administrator(self, stock_manager):
        from apps.documents.template_services import apply_starter_template

        with pytest.raises(PermissionDenied):
            apply_starter_template(
                user=stock_manager, document_type=DocumentType.DELIVERY, preset_key="classic"
            )

    def test_every_packaged_preset_renders(self, administrator):
        """Every entry in apps.documents.gallery.STARTER_TEMPLATES must
        actually produce a renderable template — this is the one guard
        against a typo'd preset value shipping broken.
        """
        from apps.documents.gallery import STARTER_TEMPLATES
        from apps.documents.template_services import apply_starter_template

        document_types = list(DocumentType.values)
        for index, preset_key in enumerate(STARTER_TEMPLATES):
            document_type = document_types[index % len(document_types)]
            reset_template(user=administrator, document_type=document_type)
            template_obj = apply_starter_template(
                user=administrator, document_type=document_type, preset_key=preset_key
            )
            assert template_obj.pk


@pytest.mark.django_db
class TestDuplicateTemplate:
    def test_copies_configuration_into_a_new_draft(self, administrator):
        source = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            accent_color="#abcdef",
        )
        new_template = duplicate_template(
            user=administrator,
            source_document_type=DocumentType.DELIVERY,
            target_document_type=DocumentType.ASSIGNMENT,
        )
        assert new_template.document_type == DocumentType.ASSIGNMENT
        assert new_template.status == "draft"
        assert new_template.html_source == source.html_source
        assert new_template.accent_color == "#abcdef"
        assert new_template.version == 1

    def test_rejects_a_broken_source_template_without_saving(self, administrator):
        """Regression test: duplicate_template() used to skip the same
        render validation update_template()/publish_template() already
        require — a template row could reach `full_clean()`+`save()` (and
        even get published later) despite html_source not actually
        rendering, breaking every future real generation from that row.
        """
        template_obj = DocumentTemplate(
            document_type=DocumentType.DELIVERY, html_source=BROKEN_HTML, version=1
        )
        template_obj.save()
        with pytest.raises(ValidationError, match="failed to render"):
            duplicate_template(
                user=administrator,
                source_document_type=DocumentType.DELIVERY,
                target_document_type=DocumentType.ASSIGNMENT,
            )
        assert get_template(DocumentType.ASSIGNMENT) is None

    def test_requires_a_different_target_type(self, administrator):
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        with pytest.raises(ValidationError, match="different document type"):
            duplicate_template(
                user=administrator,
                source_document_type=DocumentType.DELIVERY,
                target_document_type=DocumentType.DELIVERY,
            )

    def test_rejects_when_target_already_has_a_template(self, administrator):
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        update_template(
            user=administrator, document_type=DocumentType.ASSIGNMENT, html_source=VALID_HTML
        )
        with pytest.raises(ValidationError, match="already has a template"):
            duplicate_template(
                user=administrator,
                source_document_type=DocumentType.DELIVERY,
                target_document_type=DocumentType.ASSIGNMENT,
            )

    def test_requires_administrator(self, administrator, stock_manager):
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        with pytest.raises(PermissionDenied):
            duplicate_template(
                user=stock_manager,
                source_document_type=DocumentType.DELIVERY,
                target_document_type=DocumentType.ASSIGNMENT,
            )


@pytest.mark.django_db
class TestRestoreTemplateVersion:
    def test_restore_creates_a_new_version_from_an_old_snapshot(self, administrator):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            accent_color="#111111",
        )
        v1 = DocumentTemplateVersion.objects.get(
            template__document_type=DocumentType.DELIVERY, version=1
        )
        current = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            accent_color="#222222",
        )
        assert current.version == 2

        restored = restore_template_version(user=administrator, version_obj=v1)
        assert restored.pk == current.pk  # same live row, not a new one
        assert restored.accent_color == "#111111"
        assert restored.version == 3  # a new version, v1's history untouched
        v1.refresh_from_db()
        assert v1.field_snapshot["accent_color"] == "#111111"

    def test_rejects_a_broken_snapshot_without_saving(self, administrator, second_administrator):
        """Regression test: restore_template_version() used to skip
        render validation entirely — restoring a stale/corrupt snapshot
        could silently write an unrenderable html_source onto a
        *currently Published, live* template, breaking every subsequent
        real document generation for that type until someone noticed.
        """
        good = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            **PUBLISH_READY_KWARGS,
        )
        _submit_and_publish(
            document_type=DocumentType.DELIVERY,
            submitter=administrator,
            approver=second_administrator,
        )
        broken_version = DocumentTemplateVersion.objects.create(
            template=good,
            version=99,
            field_snapshot={
                "html_source": BROKEN_HTML,
                "layout_config": {},
                "accent_color": "#444444",
            },
            saved_by=administrator,
        )

        with pytest.raises(ValidationError, match="failed to render"):
            restore_template_version(user=administrator, version_obj=broken_version)

        good.refresh_from_db()
        assert good.html_source == VALID_HTML  # untouched — the bad restore never saved
        assert good.status == "published"

    def test_requires_administrator(self, administrator, stock_manager):
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        version_obj = DocumentTemplateVersion.objects.get(
            template__document_type=DocumentType.DELIVERY, version=1
        )
        with pytest.raises(PermissionDenied):
            restore_template_version(user=stock_manager, version_obj=version_obj)


@pytest.mark.django_db
class TestRenderPreviewPdf:
    def test_renders_a_real_pdf(self):
        pdf_bytes = render_preview_pdf(document_type=DocumentType.DELIVERY, html_source=VALID_HTML)
        assert pdf_bytes[:4] == b"%PDF"

    def test_raises_on_broken_template(self):
        with pytest.raises(
            Exception
        ):  # noqa: B017 - WeasyPrint/Django raise different exception types
            render_preview_pdf(document_type=DocumentType.DELIVERY, html_source=BROKEN_HTML)

    def test_uses_newly_chosen_logo_over_saved_one(self, administrator):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            logo=_png_upload("saved.png"),
        )
        logo_template = (
            "<html><body>"
            '{% if logo_data_uri %}<img src="{{ logo_data_uri }}">{% endif %}'
            "</body></html>"
        )
        pdf_bytes = render_preview_pdf(
            document_type=DocumentType.DELIVERY,
            html_source=logo_template,
            logo_file=_png_upload("new.png"),
        )
        assert pdf_bytes[:4] == b"%PDF"


@pytest.mark.django_db
class TestGenerateDocumentUsesOverride:
    def test_custom_template_is_used_when_present(
        self, administrator, second_administrator, delivery_txn
    ):
        """A brand-new template saves as Draft — real generation keeps using
        the packaged default until it's explicitly published (Draft/
        Published, request #6/#7). Publish here, matching what this test
        actually exercises.
        """
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source="<html><body><h1>OVERRIDE {{ document_number }}</h1></body></html>",
            **PUBLISH_READY_KWARGS,
        )
        _submit_and_publish(
            document_type=DocumentType.DELIVERY,
            submitter=administrator,
            approver=second_administrator,
        )
        document = generate_document(txn=delivery_txn, user=administrator)
        content = document.pdf_file.open("rb").read()
        document.pdf_file.close()
        assert content[:4] == b"%PDF"

    def test_a_draft_template_is_not_used_for_real_generation(self, administrator, delivery_txn):
        template_obj = update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        assert template_obj.status == "draft"
        document = generate_document(txn=delivery_txn, user=administrator)
        assert document.template_id is None
        assert document.template_version == "form_v1"

    def test_falls_back_to_packaged_default_when_no_override(self, administrator, delivery_txn):
        assert get_template(DocumentType.DELIVERY) is None
        document = generate_document(txn=delivery_txn, user=administrator)
        content = document.pdf_file.open("rb").read()
        document.pdf_file.close()
        assert content[:4] == b"%PDF"

    def test_template_and_version_recorded_with_a_custom_template(
        self, administrator, second_administrator, delivery_txn
    ):
        template_obj = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            **PUBLISH_READY_KWARGS,
        )
        _submit_and_publish(
            document_type=DocumentType.DELIVERY,
            submitter=administrator,
            approver=second_administrator,
        )
        document = generate_document(txn=delivery_txn, user=administrator)
        assert document.template_id == template_obj.pk
        assert document.template_version == f"v{template_obj.version}"

    def test_packaged_default_records_no_template_and_the_literal_version(
        self, administrator, delivery_txn
    ):
        document = generate_document(txn=delivery_txn, user=administrator)
        assert document.template_id is None
        assert document.template_version == "form_v1"

    def test_resetting_the_template_does_not_break_a_historical_document(
        self, administrator, second_administrator, delivery_txn
    ):
        """reset_template() deactivates (is_active=False), it never deletes
        the row (spec: "templates referenced by history may be deactivated
        but not deleted") — a later reset must never orphan the FK a
        GeneratedDocument already has, unlike this app's old
        template_obj.delete() behavior which SET_NULL'd it.
        """
        template_obj = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            **PUBLISH_READY_KWARGS,
        )
        _submit_and_publish(
            document_type=DocumentType.DELIVERY,
            submitter=administrator,
            approver=second_administrator,
        )
        document = generate_document(txn=delivery_txn, user=administrator)
        assert document.template_id is not None

        reset_template(user=administrator, document_type=DocumentType.DELIVERY)

        document.refresh_from_db()
        assert document.template_id == template_obj.pk  # still resolvable — deactivated, not gone
        template_obj.refresh_from_db()
        assert template_obj.is_active is False
        assert document.template_version == "v1"  # unchanged — a frozen snapshot, not recomputed


@pytest.mark.django_db
class TestLayoutConfig:
    """DocumentTemplate.layout_config (phase 9) — page layout, header/
    footer, notes/terms, page numbers, signature block, and column
    show/hide, composed into the rendered PDF via apps.documents.pdf.
    layout_context() rather than baked into html_source.
    """

    def test_saved_layout_config_is_used_at_generation_time(self, administrator, delivery_txn):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=(
                "<html><body>{{ header_text }}/{{ footer_text }}"
                "{% if show_page_numbers %}PAGENUMS{% endif %}</body></html>"
            ),
            layout_config={
                "header_text": "ACME HQ",
                "footer_text": "Confidential",
                "show_page_numbers": True,
            },
        )
        document = generate_document(txn=delivery_txn, user=administrator)
        content = document.pdf_file.open("rb").read()
        document.pdf_file.close()
        assert content[:4] == b"%PDF"

    def test_hidden_columns_rejects_unknown_keys(self, administrator):
        template_obj = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            layout_config={"hidden_columns": ["sku", "not_a_real_column"]},
        )
        assert template_obj.layout_config["hidden_columns"] == ["sku"]

    def test_version_starts_at_one_and_increments_on_every_save(self, administrator):
        first = update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        assert first.version == 1
        second = update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        assert second.version == 2

    def test_omitted_layout_config_keeps_previous_value(self, administrator):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=VALID_HTML,
            layout_config={"header_text": "Keep me"},
        )
        updated = update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        assert updated.layout_config["header_text"] == "Keep me"

    def test_structured_section_order_and_typography_are_preserved(self, administrator):
        template_obj = update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source=render_styleable_source(
                logo_position="left",
                accent_color="#123456",
                font_choice=FontChoice.SANS,
                page_margin=PageMargin.NORMAL,
            ),
            layout_config={
                "section_order": ["heading", "items", "details"],
                "body_font_size": 11,
                "heading_font_size": 22,
                "table_font_size": 10,
                "table_cell_padding": 8,
                "signature_left_label": "Issued by",
                "signature_right_label": "Accepted by",
            },
        )

        context = layout_context(template_obj)
        assert context["section_order"][:3] == ["heading", "items", "details"]
        assert context["body_font_size"] == 11
        assert context["signature_right_label"] == "Accepted by"
        assert "{% for section_key in section_order %}" in template_obj.html_source


@pytest.mark.django_db
class TestVisibleReportColumns:
    def test_hides_only_the_named_columns_in_the_fixed_order(self):
        from apps.documents.pdf import visible_report_columns

        columns = visible_report_columns(["sku", "accessories"])
        keys = [key for key, _ in columns]
        assert keys == ["brand", "model", "type", "serial", "quantity", "condition"]

    def test_empty_hidden_list_returns_every_column(self):
        from apps.documents.models import REPORT_COLUMNS
        from apps.documents.pdf import visible_report_columns

        assert visible_report_columns([]) == REPORT_COLUMNS
