import json

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.documents.layout import clean_presentation
from apps.documents.models import DocumentTemplate, DocumentTemplateVersion

from .test_document_templates_views import VALID_STYLE

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "blocks",
    [
        "bad",
        [{}] * 13,
        [None],
        [{"text": "x" * 5001}],
        [{"before": "../secret"}],
        [{"alignment": "left; color:red"}],
    ],
)
def test_text_blocks_are_bounded_and_validated(blocks):
    with pytest.raises(ValidationError):
        clean_presentation({"custom_blocks": blocks})


def test_text_blocks_preview_save_and_history(client, administrator):
    client.force_login(administrator)
    blocks = [
        {"text": "<script>alert(1)</script> {{ secret }}", "before": "items", "alignment": "center"}
    ]
    data = {**VALID_STYLE, "custom_blocks": json.dumps(blocks)}
    response = client.post(
        reverse("documents:template_preview", args=["delivery"]) + "?format=html", data
    )
    assert response.status_code == 200
    assert b"&lt;script&gt;" in response.content
    assert b"{{ secret }}" in response.content
    assert b"<script>" not in response.content
    assert response.content.index(b"{{ secret }}") < response.content.index(b"<thead>")
    assert (
        client.post(reverse("documents:template_edit", args=["delivery"]), data).status_code == 302
    )
    template = DocumentTemplate.objects.get(document_type="delivery")
    assert template.layout_config["custom_blocks"] == blocks
    snapshot = DocumentTemplateVersion.objects.get(template=template).field_snapshot
    assert snapshot["layout_config"]["custom_blocks"] == blocks
    response = client.post(reverse("documents:template_preview", args=["delivery"]), data)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_invalid_block_rejected_without_saving(client, administrator):
    client.force_login(administrator)
    response = client.post(
        reverse("documents:template_edit", args=["delivery"]),
        {**VALID_STYLE, "custom_blocks": '[{"alignment":"bad"}]'},
    )
    assert response.status_code == 200
    assert not DocumentTemplate.objects.exists()


@pytest.mark.parametrize("kind", ["delivery", "assignment", "disposal"])
def test_preview_uses_matching_document_type(client, administrator, kind):
    client.force_login(administrator)
    response = client.post(
        reverse("documents:template_preview", args=[kind]) + "?format=html", VALID_STYLE
    )
    assert response.status_code == 200
    html = response.content.decode()
    assert ("Certificate of Disposal" in html) == (kind == "disposal")
    assert ("Alex Morgan" in html) == (kind == "assignment")


@pytest.mark.parametrize(
    "kind, title, recipient",
    [
        ("delivery", "PRODUCT DELIVERY ACCEPTANCE", "Customer: Acme Corp"),
        ("assignment", "EQUIPMENT ASSIGNMENT", "Employee: Alex Morgan"),
        ("disposal", "EQUIPMENT DISPOSAL", "Witness: R. Patel"),
    ],
)
def test_signoff_starter_all_document_types(administrator, kind, title, recipient):
    from apps.documents.template_services import apply_starter_template, render_preview_pdf

    template = apply_starter_template(
        user=administrator, document_type=kind, preset_key="delivery_acceptance"
    )
    params = dict(
        document_type=kind,
        html_source=template.html_source,
        layout_config=template.layout_config,
        document_title=template.document_title,
    )
    html = render_preview_pdf(**params, output_format="html")
    assert title in html
    assert recipient in html
    assert "DOC-000123" in html
    assert "15 January 2026" in html
    assert html.count("data-editor-column=") == 4
    assert "acceptance-reference" in html
    assert "acceptance-signatures" in html
    assert render_preview_pdf(**params).startswith(b"%PDF")
