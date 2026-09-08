"""Opt-in real-browser QA of staged lifecycle corrections."""

import os
from pathlib import Path

import pytest
from django.urls import reverse

from apps.imports import services

from .test_imports_services import _base_row, _csv_upload

pytestmark = [
    pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser QA"),
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
]


def test_admin_reviews_and_imports_internal_installation(live_server, administrator, location_tree):
    from playwright.sync_api import expect, sync_playwright

    batch, _ = services.create_batch_from_upload(
        user=administrator,
        uploaded_file=_csv_upload([_base_row(LOCATION="", **{"Arrival Date": "2026-01-01"})]),
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "response",
            lambda response: errors.append(response.url) if response.status >= 400 else None,
        )
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        page.goto(live_server.url + reverse("login"))
        page.locator('[name="username"]').fill(administrator.username)
        page.locator('[name="password"]').fill("a-strong-test-password-123")
        page.get_by_role("button", name="Log in").click()
        page.wait_for_url(live_server.url + "/")
        page.goto(live_server.url + batch.get_absolute_url())
        page.get_by_role("link", name="Review status / source room").click()
        page.locator('[name="import_status"]').select_option("in_use")
        page.locator('[name="location"]').select_option(str(location_tree["room"].pk))
        page.locator('[name="movement_date"]').fill("2026-01-02")
        page.locator('[name="installation_notes"]').fill("Installed in server room X")
        screenshots = Path(".qa-screenshots")
        screenshots.mkdir(exist_ok=True)
        for width, height in [(1366, 768), (1440, 900)]:
            page.set_viewport_size({"width": width, "height": height})
            page.locator("h1").click()
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(screenshots / f"import-status-review-{width}.png"))
        page.get_by_role("button", name="Save review").click()
        expect(page.get_by_text("Status and source room reviewed.", exact=True)).to_be_visible()
        page.get_by_role("button", name="Execute", exact=False).click()
        expect(page.get_by_role("table").get_by_text("Imported", exact=True)).to_be_visible()
        assert not errors, errors
        browser.close()
    assert batch.rows.get().created_unit_asset.status == "in_use"
