"""Opt-in keyboard/accessibility smoke test for the shared application shell."""

import os

import pytest
from django.urls import reverse

pytestmark = [
    pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser QA"),
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
]


def test_keyboard_shell_and_core_pages(live_server, administrator):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(live_server.url + reverse("login"))
        page.locator('[name="username"]').fill(administrator.username)
        page.locator('[name="password"]').fill("a-strong-test-password-123")
        page.get_by_role("button", name="Log in").click()
        page.wait_for_url(live_server.url + "/")

        page.keyboard.press("Tab")
        expect(page.get_by_role("link", name="Skip to main content")).to_be_focused()
        page.keyboard.press("Enter")
        expect(page.locator("#main-content")).to_be_focused()

        page.locator(".user-menu summary").click()
        expect(page.locator(".user-menu")).to_have_attribute("open", "")
        page.keyboard.press("Escape")
        expect(page.locator(".user-menu")).not_to_have_attribute("open", "")

        for route in ("core:job_list", "settings:notification_preferences", "inventory:asset_list"):
            page.goto(live_server.url + reverse(route))
            expect(page.locator("h1")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

        assert not errors, errors
        browser.close()
