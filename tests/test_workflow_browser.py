# ruff: noqa: F811
import os

import pytest
from django.urls import reverse

pytestmark = [
    pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser QA"),
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
]


def test_delivery_review_dialog_and_dashboard_reorder(live_server, administrator):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        page.goto(live_server.url + reverse("login"))
        page.locator('[name="username"]').fill(administrator.username)
        page.locator('[name="password"]').fill("a-strong-test-password-123")
        page.get_by_role("button", name="Log in").click()
        page.goto(live_server.url + reverse("core:dashboard_preferences"))
        first = page.locator("[data-card-row]").first
        first.get_by_role("button", name="Move", exact=False).nth(1).click()
        page.get_by_role("button", name="Save").click()
        page.goto(live_server.url + reverse("inventory:deliver"))
        page.locator('[name="final_customer"]').fill("Browser customer")
        page.locator('form[data-review-submit] button[type="submit"]').click()
        expect(page.locator("dialog.confirmation-dialog")).to_be_visible()
        expect(page.get_by_text("Review before completing")).to_be_visible()
        browser.close()
