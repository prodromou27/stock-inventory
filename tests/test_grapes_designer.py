import json
import os
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.template import Context, Template
from django.urls import reverse

from apps.documents.designer_services import compile_design, save_design
from apps.documents.models import DocumentTemplate, DocumentTemplateVersion
from apps.documents.template_services import template_completeness

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("kind", ["delivery", "assignment", "disposal"])
def test_save_and_use_activates_for_operations(client, administrator, kind):
    from apps.documents.pdf import active_template_for

    client.force_login(administrator)
    response = client.post(
        reverse("documents:template_designer", args=[kind]),
        {
            "action": "save",
            "activate": "true",
            "version": "0",
            "design": json.dumps({"html": "<h1>New operational design</h1>", "css": ""}),
        },
    )
    assert response.status_code == 200
    current = active_template_for(kind)
    assert current is not None
    assert "New operational design" in current.html_source
    assert current.approved_by == administrator
    assert current.version == 1


def test_settings_save_preserves_editable_canvas(administrator):
    from apps.documents.template_services import update_template

    design = {"html": "<p>Saved canvas</p>", "css": ""}
    saved = save_design(user=administrator, document_type="delivery", design=design, version=0)
    updated = update_template(
        user=administrator,
        document_type="delivery",
        html_source=saved.html_source,
        layout_config={},
        company_name="New name",
    )
    assert updated.layout_config["visual_design"] == design
    assert (
        DocumentTemplateVersion.objects.get(template=updated, version=2).field_snapshot[
            "layout_config"
        ]["visual_design"]
        == design
    )


def test_page_setup_compiles():
    source = compile_design(
        {
            "html": "<p>Landscape</p>",
            "css": "",
            "page": {
                "size": "Letter",
                "orientation": "landscape",
                "margin": 12,
            },
        }
    )
    assert "size:Letter landscape;margin:12mm" in source


@pytest.mark.parametrize(
    "page",
    [
        {"margin": True},
        {"margin": 0},
        {"margin": 41},
        {"size": "url(x)"},
        {"orientation": "other"},
        {"extra": 1},
    ],
)
def test_page_setup_rejects_invalid_values(page):
    with pytest.raises(ValidationError):
        compile_design({"html": "", "css": "", "page": page})


def test_local_designer_assets():
    from django.contrib.staticfiles.finders import find

    for asset in (
        "vendor/grapesjs/grapes.min.js",
        "vendor/grapesjs/grapes.min.css",
        "vendor/grapesjs/font-awesome/fonts/fontawesome-webfont.woff2",
        "js/visual_designer.js",
    ):
        assert find(asset), asset


def test_published_design_snapshot_and_restore(administrator, unit_product, location_tree):
    from datetime import date

    from apps.documents.services import generate_document
    from apps.documents.template_services import restore_template_version
    from apps.inventory.models import UnitAsset
    from apps.inventory.services.assignments import deliver_to_customer
    from apps.inventory.services.receipts import receive_stock

    design = {
        "html": '<h1>My delivery</h1><span data-stock-field="final_customer">Sample</span>',
        "css": "",
    }
    template = save_design(
        user=administrator,
        document_type="delivery",
        design=design,
        version=0,
        preview_confirmed=True,
        activate=True,
    )
    original_version = DocumentTemplateVersion.objects.get(template=template)
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="VISUAL-PDF",
    )
    asset = UnitAsset.objects.get(vendor_serial="VISUAL-PDF")
    txn = deliver_to_customer(
        user=administrator,
        final_customer="Real customer",
        occurred_at=date.today(),
        unit_asset_ids=[asset.pk],
    )
    document = generate_document(txn=txn, user=administrator)
    assert document.template_id == template.pk
    assert document.context_snapshot["final_customer"] == "Real customer"
    with document.pdf_file.open("rb") as pdf:
        original_pdf = pdf.read()
    assert original_pdf.startswith(b"%PDF")
    save_design(
        user=administrator, document_type="delivery", design={"html": "", "css": ""}, version=1
    )
    document.refresh_from_db()
    with document.pdf_file.open("rb") as pdf:
        assert pdf.read() == original_pdf
    restored = restore_template_version(user=administrator, version_obj=original_version)
    assert restored.layout_config["visual_design"] == design


@pytest.mark.parametrize(
    "html,css",
    [
        ("<script>alert(1)</script>", ""),
        ('<p onclick="x()">x</p>', ""),
        ('<img src="http://127.0.0.1/secret">', ""),
        ('<p data-stock-field="user.password">x</p>', ""),
        ('<span data-stock-field="line.serial">x</span>', ""),
        ("<p>x</p>", "p{background-color:url(http://localhost)}"),
        ("<p>x</p>", '@import "http://localhost";'),
        ("<p>x</p>", "p{color:red;}body{display:expression(x)}"),
    ],
)
def test_reject_unsafe_design(html, css):
    with pytest.raises(ValidationError):
        compile_design({"html": html, "css": css})


def test_design_is_literal_except_inventory_fields():
    source = compile_design(
        {
            "html": '<p>{{ secret }} {% include "secret" %}</p>'
            '<span data-stock-field="final_customer">Sample</span>'
            '<table><tbody><tr data-stock-row="true"><td>'
            '<span data-stock-field="line.serial">Example</span></td></tr></tbody></table>',
            "css": "",
        }
    )
    html = Template(source).render(
        Context(
            {
                "secret": "SECRET",
                "final_customer": "<Customer>",
                "lines": [{"serial": "A"}, {"serial": "B"}],
            }
        )
    )
    assert "SECRET" not in html
    assert "&lt;Customer&gt;" in html
    assert html.count("<tr>") == 2
    assert "Prepared by:" not in html


def test_save_versions_and_stale_tab(administrator, stock_manager):
    design = {"html": "<p>Only my content</p>", "css": "p{color:#123456}"}
    saved = save_design(user=administrator, document_type="delivery", design=design, version=0)
    assert saved.layout_config["visual_design"] == design
    assert (
        DocumentTemplateVersion.objects.get(template=saved).field_snapshot["layout_config"][
            "visual_design"
        ]
        == design
    )
    assert [c["key"] for c in template_completeness(saved)] == ["preview"]
    with pytest.raises(ValidationError, match="another tab"):
        save_design(user=administrator, document_type="delivery", design=design, version=0)
    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        save_design(user=stock_manager, document_type="delivery", design=design, version=1)


@pytest.mark.parametrize("kind", ["delivery", "assignment", "disposal"])
def test_designer_endpoint(client, administrator, stock_manager, kind):
    url = reverse("documents:template_designer", args=[kind])
    client.force_login(stock_manager)
    assert client.get(url).status_code == 403
    assert client.post(url, {"action": "save"}).status_code == 403
    client.force_login(administrator)
    assert client.get(url).status_code == 200
    data = {"design": json.dumps({"html": "<p>Custom only</p>", "css": ""}), "action": "pdf"}
    response = client.post(url, data)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    response = client.post(url, {**data, "action": "save", "version": "0"})
    assert response.status_code == 200
    assert (
        DocumentTemplate.objects.get(document_type=kind).layout_config["visual_design"]["html"]
        == "<p>Custom only</p>"
    )


@pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser QA")
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_grapes_browser(live_server, administrator):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda request: (
                errors.append(f"External request: {request.url}")
                if request.url.startswith(("http:", "https:"))
                and not request.url.startswith(live_server.url + "/")
                else None
            ),
        )
        page.on(
            "response",
            lambda response: (
                errors.append(f"HTTP {response.status}: {response.url}")
                if response.status >= 400
                else None
            ),
        )
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(live_server.url + reverse("login"))
        page.locator("[name=username]").fill(administrator.username)
        page.locator("[name=password]").fill("a-strong-test-password-123")
        page.get_by_role("button", name="Log in").click()
        page.wait_for_url(live_server.url + "/")
        for kind in ["delivery", "assignment", "disposal"]:
            page.goto(live_server.url + reverse("documents:template_designer", args=[kind]))
            expect(page.locator("#designer-status")).to_contain_text("Starter loaded")
            page.frame_locator(".gjs-frame").locator("th").first.click()
            page.get_by_text("Asset table columns", exact=True).click()
            page.locator("#designer-column").select_option("model")
            page.locator("#designer-add-column").click()
            expect(page.frame_locator(".gjs-frame").locator("th")).to_have_count(5)
            page.frame_locator(".gjs-frame").locator("th").last.click()
            page.locator("#designer-remove-column").click()
            expect(page.frame_locator(".gjs-frame").locator("th")).to_have_count(4)
            page.locator("#designer-preview").click()
            expect(page.locator("#designer-status")).to_contain_text(
                "Preview generated", timeout=15000
            )
            page.locator("#designer-close").click()
            page.evaluate(
                "void grapesjs.editors[0].getWrapper().append("
                "{type:'text',tagName:'p',content:'My unique declaration'})"
            )
            text = page.frame_locator(".gjs-frame").get_by_text("My unique declaration", exact=True)
            text.click()
            text.dblclick()
            expect(text).to_have_attribute("contenteditable", "true")
            page.keyboard.press("Control+A")
            page.keyboard.insert_text("My edited declaration")
            page.locator("#designer-status").click()
            page.locator("#designer-save").click()
            expect(page.locator("#designer-status")).to_have_text(
                "Saved as version 1", timeout=30000
            )
            page.reload()
            expect(page.locator("#designer-status")).to_have_text("Saved design loaded")
            assert "My edited declaration" in page.evaluate("grapesjs.editors[0].getHtml()")
            Path(".qa-screenshots").mkdir(exist_ok=True)
            page.screenshot(path=f".qa-screenshots/grapes-{kind}.png")
        page.set_viewport_size({"width": 1366, "height": 768})
        page.screenshot(path=".qa-screenshots/grapes-laptop.png")
        page.get_by_text("Start over", exact=True).click()
        page.locator("#designer-blank").click()
        page.locator("#designer-save").click()
        expect(page.locator("#designer-status")).to_have_text("Saved as version 2", timeout=30000)
        page.reload()
        expect(page.locator("#designer-status")).to_have_text("Saved design loaded")
        assert not page.evaluate("grapesjs.editors[0].getWrapper().components().length")
        assert not errors, errors
        browser.close()
