"""Opt-in browser QA: RUN_BROWSER_TESTS=1 python -m pytest ... -s."""

import os
from pathlib import Path

import pytest
from django.urls import reverse

pytestmark = [
    pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser QA"),
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
]


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
            expect(page.locator("#template-preview-frame")).not_to_have_attribute("hidden", "")
            page.locator('[name="document_title"]').fill("QA delivery form")
            expect(
                page.locator("#template-preview-frame").content_frame.locator("h1")
            ).to_have_text("QA delivery form")
            page.screenshot(path=str(screenshots / f"editor-{width}.png"), full_page=True)
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
