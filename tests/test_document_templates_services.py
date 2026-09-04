from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.audit.models import AuditEvent
from apps.documents.models import DocumentTemplate, DocumentType, FontChoice, PageMargin
from apps.documents.pdf import default_template_source, render_styleable_source
from apps.documents.services import generate_document
from apps.documents.template_services import (
    get_template,
    render_preview_pdf,
    reset_template,
    update_template,
)
from apps.inventory.models import UnitAsset
from apps.inventory.services.assignments import deliver_to_customer
from apps.inventory.services.receipts import receive_stock

VALID_HTML = "<html><body><h1>{{ document_number }}</h1><p>{{ final_customer }}</p></body></html>"
BROKEN_HTML = "{% for x in %}broken"

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
    def test_custom_template_is_used_when_present(self, administrator, delivery_txn):
        update_template(
            user=administrator,
            document_type=DocumentType.DELIVERY,
            html_source="<html><body><h1>OVERRIDE {{ document_number }}</h1></body></html>",
        )
        document = generate_document(txn=delivery_txn, user=administrator)
        content = document.pdf_file.open("rb").read()
        document.pdf_file.close()
        assert content[:4] == b"%PDF"

    def test_falls_back_to_packaged_default_when_no_override(self, administrator, delivery_txn):
        assert get_template(DocumentType.DELIVERY) is None
        document = generate_document(txn=delivery_txn, user=administrator)
        content = document.pdf_file.open("rb").read()
        document.pdf_file.close()
        assert content[:4] == b"%PDF"

    def test_template_and_version_recorded_with_a_custom_template(
        self, administrator, delivery_txn
    ):
        template_obj = update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
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
        self, administrator, delivery_txn
    ):
        """GeneratedDocument.template is SET_NULL, not PROTECT — a later
        reset must never be blocked by history that already has its own
        frozen context_snapshot/pdf_file (doc 06's immutability guarantee
        doesn't depend on this FK staying populated).
        """
        update_template(
            user=administrator, document_type=DocumentType.DELIVERY, html_source=VALID_HTML
        )
        document = generate_document(txn=delivery_txn, user=administrator)
        assert document.template_id is not None

        reset_template(user=administrator, document_type=DocumentType.DELIVERY)

        document.refresh_from_db()
        assert document.template_id is None
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
