"""Opt-in real browser checks: RUN_BROWSER_TESTS=1 pytest this file."""

import os
import re
from datetime import date
from pathlib import Path

import pytest
from django.urls import reverse

from apps.inventory.models import UnitAsset
from apps.inventory.services.assignments import deliver_to_customer
from apps.inventory.services.grid_views import create_saved_grid_view
from apps.inventory.services.receipts import receive_stock

pytestmark = [
    pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser QA"),
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
]


def test_stock_manager_room_filters_and_delivery(
    live_server, stock_manager_with_room_access, unit_product, location_tree
):
    from playwright.sync_api import expect, sync_playwright

    manager = stock_manager_with_room_access
    for serial in ["ROOM-STOCK", "DELIVERED-HISTORY"]:
        receive_stock(
            user=manager,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial=serial,
        )
    delivered = UnitAsset.objects.get(vendor_serial="DELIVERED-HISTORY")
    deliver_to_customer(
        user=manager,
        final_customer="Existing customer",
        occurred_at=date.today(),
        unit_asset_ids=[delivered.pk],
    )
    create_saved_grid_view(
        user=manager,
        grid_key="assets",
        name="My default",
        is_default=True,
        state={"headerFilters": [{"field": "status", "value": "delivered"}]},
    )
    screenshots = Path(".qa-screenshots")
    screenshots.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors, failures = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        page.on(
            "response",
            lambda response: failures.append(response.url) if response.status >= 400 else None,
        )
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(live_server.url + reverse("login"))
        page.locator('[name="username"]').fill(manager.username)
        page.locator('[name="password"]').fill("a-strong-test-password-123")
        page.get_by_role("button", name="Log in").click()
        page.wait_for_url(live_server.url + "/")
        expect(page.locator(".stat-card")).to_have_count(4)
        for width, height in [(1440, 900), (1366, 768)]:
            page.set_viewport_size({"width": width, "height": height})
            page.screenshot(path=str(screenshots / f"workspace-dashboard-{width}.png"))
        page.get_by_role("link", name="Customize dashboard").click()
        page.locator('[name="visible_cards"][value="delivered_count"]').uncheck()
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator(".stat-card")).to_have_count(3)
        page.get_by_role("link", name="Open room stock").click()
        rows = page.locator("#asset-grid-table .tabulator-row")
        expect(rows).to_have_count(1)
        expect(rows).to_contain_text("ROOM-STOCK")
        # Wait for default-view loading as well: a late default must not
        # overwrite the explicit in-storage shortcut.
        expect(page.locator("#asset-grid-saved-views option")).to_have_count(2)
        expect(rows).to_contain_text("ROOM-STOCK")
        page.screenshot(path=str(screenshots / "workspace-room-stock.png"))
        url = live_server.url + reverse("inventory:asset_list")
        page.goto(url + "?type=Firewall&q=ROOM-STOCK&location=" + str(location_tree["room"].pk))
        expect(page.locator("#asset-grid-search")).to_have_value("ROOM-STOCK")
        expect(rows).to_have_count(1)
        expect(rows).to_contain_text("ROOM-STOCK")
        expect(page.locator("#asset-grid-export-filtered")).to_have_attribute(
            "href", re.compile("location=")
        )
        page.goto(url + "?type=Server")
        expect(rows).to_have_count(0)
        page.goto(url + "?in_storage=1")
        expect(rows).to_have_count(1)
        rows.locator('input[type="checkbox"]').check()
        page.get_by_role("link", name="Deliver selected").click()
        expect(page.locator("#asset-picker-selection-count")).to_contain_text("1")
        page.locator('[name="final_customer"]').fill("Workspace customer")
        page.locator('[name="project_reference"]').fill("WORK-2026")
        page.screenshot(path=str(screenshots / "workspace-delivery.png"))
        page.get_by_role("button", name="Complete delivery & generate PDF").click()
        page.wait_for_url("**/documents/*/")
        pdf_url = page.get_by_role("link", name="Download PDF").get_attribute("href")
        assert page.request.get(live_server.url + pdf_url).body().startswith(b"%PDF")
        page.goto(url + "?in_storage=1")
        expect(rows).to_have_count(0)
        for route in [
            "inventory:receive_bulk",
            "inventory:assign",
            "inventory:balance_list",
            "settings:hub",
        ]:
            page.goto(live_server.url + reverse(route))
            page.screenshot(path=str(screenshots / f"workspace-{route.replace(':', '-')}.png"))
        page.goto(live_server.url + reverse("core:home"))
        page.locator("#theme-toggle").click()
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        page.screenshot(
            path=str(screenshots / "workspace-dashboard-dark.png"), animations="disabled"
        )
        assert errors == []
        assert failures == []
        browser.close()
