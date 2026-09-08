"""Opt-in browser QA: RUN_BROWSER_TESTS=1 python -m pytest ... -s."""

import os
from pathlib import Path

import pytest
from django.urls import reverse

pytestmark = [
    pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser QA"),
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
]


def test_signoff_design_for_all_document_types(live_server, administrator):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("dialog", lambda dialog: dialog.accept())
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_server.url + reverse("login"))
        page.locator('[name="username"]').fill(administrator.username)
        page.locator('[name="password"]').fill("a-strong-test-password-123")
        page.get_by_role("button", name="Log in").click()
        page.wait_for_url(live_server.url + "/")
        for kind, title in [
            ("delivery", "PRODUCT DELIVERY ACCEPTANCE AND SIGN OFF"),
            ("assignment", "EQUIPMENT ASSIGNMENT AND ACCEPTANCE"),
            ("disposal", "EQUIPMENT DISPOSAL CERTIFICATE"),
        ]:
            page.goto(live_server.url + reverse("documents:template_edit", args=[kind]))
            page.locator("#template-acceptance-preset").click()
            preview = page.locator("#template-preview-frame").content_frame
            expect(preview.locator("h1")).to_have_text(title)
            expect(preview.locator("[data-editor-column]")).to_have_count(4)
            expect(preview.locator(".acceptance-signatures")).to_have_count(1)
            page.get_by_role("button", name="Save", exact=True).click()
            expect(page.locator('[name="layout_variant"]')).to_have_value("acceptance")
            expect(preview.locator("h1")).to_have_text(title)
            page.evaluate(
                "window.scrollTo(0, document.querySelector('.template-editor-layout')"
                ".getBoundingClientRect().top + window.scrollY - 90)"
            )
            Path(".qa-screenshots").mkdir(exist_ok=True)
            page.screenshot(path=f".qa-screenshots/signoff-{kind}.png")
        assert not errors, errors
        browser.close()


def test_document_editor_browser(live_server, administrator, stock_manager, location_tree):
    from playwright.sync_api import expect, sync_playwright

    screenshots = Path(".qa-screenshots")
    screenshots.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(live_server.url + reverse("login"))
        page.locator('[name="username"]').fill(administrator.username)
        page.locator('[name="password"]').fill("a-strong-test-password-123")
        page.locator('button[type="submit"]').click()
        page.wait_for_url(live_server.url + "/")
        for width, height in [(1440, 900), (1366, 768)]:
            page.set_viewport_size({"width": width, "height": height})
            page.goto(live_server.url + reverse("documents:template_edit", args=["delivery"]))
            expect(page.locator("#template-paper")).not_to_have_attribute("hidden", "")
            expect(page.locator(".template-paper__margin-guide")).to_be_visible()
            # The margin guide is a visual hint, not decoration — it must
            # actually shrink to match a wider page_margin preset.
            normal_guide = page.locator(".template-paper__margin-guide").bounding_box()
            page.select_option('[name="page_margin"]', "spacious")
            page.wait_for_timeout(1000)
            spacious_guide = page.locator(".template-paper__margin-guide").bounding_box()
            assert spacious_guide["width"] < normal_guide["width"]
            page.select_option('[name="page_margin"]', "normal")
            page.wait_for_timeout(1000)
            advanced_layout = page.locator(
                "details.template-editor__advanced", has_text="Advanced visual layout"
            )
            expect(advanced_layout).not_to_have_attribute("open", "")
            expect(page.locator('[name="column_labels"]')).not_to_be_visible()
            advanced_layout.locator("summary").click()
            expect(page.locator("#section-layout")).to_be_visible()
            page.locator('[name="document_title"]').fill("QA delivery form")
            expect(
                page.locator("#template-preview-frame").content_frame.locator("h1")
            ).to_have_text("QA delivery form")
            title = page.locator("#template-preview-frame").content_frame.locator("h1")
            expect(title).to_have_attribute("contenteditable", "plaintext-only")
            title.fill("Edited on the paper")
            page.locator("h1").first.click()
            expect(page.locator('[name="document_title"]')).to_have_value("Edited on the paper")
            page.get_by_role("button", name="Add text block", exact=True).click()
            page.get_by_role("textbox", name="Text block 1 wording").fill(
                "Equipment must be returned in good condition."
            )
            expect(
                page.locator("#template-preview-frame").content_frame.locator(".custom-text-block")
            ).to_have_text("Equipment must be returned in good condition.")
            page.get_by_role("checkbox", name="Show SKU column", exact=True).uncheck()
            expect(
                page.locator("#template-preview-frame").content_frame.locator(
                    '[data-editor-column="sku"]'
                )
            ).to_have_count(0)
            assert page.locator("#template-paper-viewport").bounding_box()["width"] > 500
            page.evaluate(
                "window.scrollTo(0, document.querySelector('.template-editor-layout')"
                ".getBoundingClientRect().top + window.scrollY - 90)"
            )
            page.screenshot(path=str(screenshots / f"editor-{width}.png"))
            print("PREVIEW", page.locator("#template-preview-empty").text_content())
        for route in [
            "core:home",
            "inventory:receive_stock",
            "inventory:transfer",
            "inventory:reserve",
            "inventory:assign",
            "inventory:deliver",
            "inventory:asset_list",
        ]:
            response = page.goto(live_server.url + reverse(route))
            assert response.status == 200
            page.screenshot(path=str(screenshots / f"{route.replace(':', '-')}.png"))
        manager_context = browser.new_context()
        manager_page = manager_context.new_page()
        manager_page.goto(live_server.url + reverse("login"))
        manager_page.locator('[name="username"]').fill(stock_manager.username)
        manager_page.locator('[name="password"]').fill("a-strong-test-password-123")
        manager_page.get_by_role("button", name="Log in").click()
        manager_page.goto(live_server.url + reverse("documents:template_hub"))
        assert manager_page.locator("body").inner_text().find("Edit") == -1
        manager_context.close()
        assert errors == []
        browser.close()


def test_document_generation_browser(live_server, administrator, unit_product, location_tree):
    """End-to-end: receive a unit, deliver it, click "Generate printable
    document" on the transaction page (a plain form POST, not JS), and
    confirm the resulting document's "Download PDF" link actually serves a
    real PDF — the full path a user takes, not just the service function
    called directly (which the non-browser test suite already covers).
    """
    from datetime import date

    from playwright.sync_api import sync_playwright

    from apps.inventory.models import UnitAsset
    from apps.inventory.services.assignments import deliver_to_customer
    from apps.inventory.services.receipts import receive_stock

    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="SN-QA-GENDOC",
    )
    asset = UnitAsset.objects.get(vendor_serial="SN-QA-GENDOC")
    txn = deliver_to_customer(
        user=administrator,
        final_customer="Browser QA Corp",
        occurred_at=date.today(),
        unit_asset_ids=[asset.pk],
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(live_server.url + reverse("login"))
        page.locator('[name="username"]').fill(administrator.username)
        page.locator('[name="password"]').fill("a-strong-test-password-123")
        page.locator('button[type="submit"]').click()
        page.wait_for_url(live_server.url + "/")

        page.goto(live_server.url + reverse("inventory:transaction_detail", args=[txn.pk]))
        page.get_by_role("button", name="Generate printable document").click()
        # The generated document gets its own new pk (unrelated to the
        # transaction's), so the exact post-redirect URL can't be known in
        # advance — matching the documents-detail path shape is enough.
        page.wait_for_url("**/documents/*/")

        download_link = page.get_by_role("link", name="Download PDF")
        pdf_url = download_link.get_attribute("href")
        response = page.request.get(live_server.url + pdf_url)
        assert response.ok
        assert response.body()[:4] == b"%PDF"
        assert errors == []
        browser.close()


def test_template_publish_and_restore_browser(live_server, administrator, second_administrator):
    """Draft -> Submit for review -> Approve and publish (by a *different*
    Administrator) -> edit again -> Restore an earlier version, driven
    through the actual editor UI rather than calling the service functions
    directly — catches a wiring mistake in the buttons/forms themselves
    that a pure-Python test of template_services.py never would.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        # Restore's form carries data-confirm (static/js/app.js's generic
        # window.confirm() guard) — Playwright auto-dismisses native dialogs
        # by default, which would silently block the submission and leave
        # the page looking unchanged (no error, just nothing happens).
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(live_server.url + reverse("login"))
        page.locator('[name="username"]').fill(administrator.username)
        page.locator('[name="password"]').fill("a-strong-test-password-123")
        page.locator('button[type="submit"]').click()
        page.wait_for_url(live_server.url + "/")

        edit_url = live_server.url + reverse("documents:template_edit", args=["delivery"])
        page.goto(edit_url)
        page.locator('[name="document_title"]').fill("QA v1 title")
        # submit_for_review() gates on template_completeness() — satisfy the
        # rest of the checklist (title is filled above; signatures/columns
        # already default to satisfied) so the upcoming submit click below
        # actually succeeds instead of failing the completeness check.
        page.locator('[name="company_name"]').fill("QA Corp")
        page.locator('[name="logo_intentionally_omitted"]').check()
        page.locator('[name="preview_confirmed"]').check()
        page.get_by_role("button", name="Save").click()
        page.wait_for_url(edit_url)
        # The badge's text-transform: uppercase CSS changes how Playwright's
        # inner_text() (rendered text) reports it, not just how it looks —
        # match case-insensitively rather than depend on that styling.
        assert "draft" in page.locator(".page-header__actions").inner_text().lower()

        page.get_by_role("button", name="Submit for review").click()
        page.wait_for_url(edit_url)
        assert "pending review" in page.locator(".page-header__actions").inner_text().lower()
        # The submitter themselves must not see an Approve button — a
        # different Administrator does the approving, in a separate context.
        assert page.get_by_role("button", name="Approve and publish").count() == 0

        reviewer_context = browser.new_context()
        reviewer_page = reviewer_context.new_page()
        reviewer_page.goto(live_server.url + reverse("login"))
        reviewer_page.locator('[name="username"]').fill(second_administrator.username)
        reviewer_page.locator('[name="password"]').fill("a-strong-test-password-123")
        reviewer_page.get_by_role("button", name="Log in").click()
        reviewer_page.goto(edit_url)
        reviewer_page.get_by_role("button", name="Approve and publish").click()
        reviewer_page.wait_for_url(edit_url)
        assert "published" in reviewer_page.locator(".page-header__actions").inner_text().lower()
        reviewer_context.close()

        page.reload()
        assert "published" in page.locator(".page-header__actions").inner_text().lower()

        page.locator('[name="document_title"]').fill("QA v2 title")
        page.get_by_role("button", name="Save").click()
        page.wait_for_url(edit_url)

        history_rows = page.locator("table tbody tr")
        restore_button = history_rows.filter(has_text="v1").get_by_role("button", name="Restore")
        restore_button.click()
        page.wait_for_url(edit_url)
        assert page.locator('[name="document_title"]').input_value() == "QA v1 title"

        browser.close()


def test_custom_html_editing_drives_the_live_preview(live_server, administrator, location_tree):
    """Toggling on raw HTML/CSS and typing a template actually changes what
    the live preview iframe renders — catches a wiring mistake between the
    checkbox/textarea and refreshPreview()'s form submission that a pure-
    Python test of the view/service layer never would.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(live_server.url + reverse("login"))
        page.locator('[name="username"]').fill(administrator.username)
        page.locator('[name="password"]').fill("a-strong-test-password-123")
        page.locator('button[type="submit"]').click()
        page.wait_for_url(live_server.url + "/")
        page.goto(live_server.url + reverse("documents:template_edit", args=["delivery"]))

        details = page.locator(
            "details.template-editor__advanced", has_text="Developer source (optional)"
        )
        details.locator("summary").click()
        page.locator('[name="custom_html_enabled"]').check()
        page.locator('[name="custom_html_source"]').fill(
            '<html><body><h1 style="color:red">CUSTOM HTML WORKS '
            "{{ document_number }}</h1></body></html>"
        )
        page.wait_for_timeout(1200)
        frame = page.locator("#template-preview-frame").content_frame
        assert "CUSTOM HTML WORKS" in frame.locator("h1").text_content()
        assert errors == []

        page.get_by_role("button", name="Save").click()
        page.wait_for_url(live_server.url + reverse("documents:template_edit", args=["delivery"]))
        # Reload to confirm the save round-tripped through the database, not
        # just the in-progress form state — checked and prefilled from the
        # persisted row (this ORM-free check runs before browser.close() so
        # the assertion still uses the live rendered page, not a direct
        # query, since a direct ORM call would fight Playwright's sync API
        # for the current thread's asyncio event loop).
        assert page.locator('[name="custom_html_enabled"]').is_checked()
        assert "CUSTOM HTML WORKS" in page.locator('[name="custom_html_source"]').input_value()

        browser.close()
