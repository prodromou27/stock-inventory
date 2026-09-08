# ruff: noqa: F811 -- imported pytest fixture is injected by name
import os
from pathlib import Path

import pytest
from django.urls import reverse

from .test_location_inventory_scope import stocked_hierarchy  # noqa: F401

pytestmark = [
    pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser QA"),
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
]


def test_country_room_shelf_controls(live_server, administrator, location_tree, stocked_hierarchy):
    from playwright.sync_api import expect, sync_playwright

    room, shelf, _ = stocked_hierarchy
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "response",
            lambda response: errors.append(response.url) if response.status >= 400 else None,
        )
        page.goto(live_server.url + reverse("login"))
        page.locator('[name="username"]').fill(administrator.username)
        page.locator('[name="password"]').fill("a-strong-test-password-123")
        page.get_by_role("button", name="Log in").click()
        page.wait_for_url(live_server.url + "/")
        page.goto(live_server.url + reverse("inventory:asset_list") + f"?location={room.pk}")
        rows = page.locator("#asset-grid-table .tabulator-row")
        expect(rows).to_have_count(2)
        expect(page.locator("#inventory-country")).to_have_value(
            f'id:{location_tree["country"].pk}'
        )
        page.locator("#inventory-room").select_option(str(shelf.pk))
        expect(page.locator("#inventory-country")).to_have_value(
            f'id:{location_tree["country"].pk}'
        )
        page.get_by_role("button", name="View location").click()
        expect(rows).to_have_count(1)
        page.locator("#inventory-country").select_option(f'id:{location_tree["country"].pk}')
        expect(page.locator("#inventory-room")).to_have_value("")
        page.get_by_role("button", name="View location").click()
        expect(rows).to_have_count(3)
        # Exercise real Tabulator filters, including automatic ancestors and clearing descendants.
        page.evaluate(
            "value => Tabulator.findTable('#asset-grid-table')[0]"
            ".setHeaderFilterValue('shelf', value)",
            f"id:{shelf.pk}",
        )
        expect(rows).to_have_count(1)
        assert (
            page.evaluate(
                "Tabulator.findTable('#asset-grid-table')[0].getHeaderFilterValue('storage_room')"
            )
            == f"id:{room.pk}"
        )
        page.evaluate(
            "Tabulator.findTable('#asset-grid-table')[0].setHeaderFilterValue('country', '')"
        )
        expect(rows).to_have_count(3)
        Path(".qa-screenshots").mkdir(exist_ok=True)
        page.screenshot(path=".qa-screenshots/location-filters-1366.png")
        country_box = page.locator("#inventory-country").bounding_box()
        room_box = page.locator("#inventory-room").bounding_box()
        label_box = page.locator('label[for="inventory-country"]').bounding_box()
        assert abs(country_box["y"] - room_box["y"]) < 2
        assert label_box["y"] + label_box["height"] <= country_box["y"]
        page.set_viewport_size({"width": 1440, "height": 900})
        page.screenshot(path=".qa-screenshots/location-filters-1440.png")
        page.goto(live_server.url + reverse("inventory:balance_list") + f"?location={room.pk}")
        expect(page.locator("#balance-grid-table .tabulator-row")).to_have_count(2)
        expect(page.locator("#inventory-country")).to_have_value(
            f'id:{location_tree["country"].pk}'
        )
        page.locator("#inventory-country").select_option(f'id:{location_tree["country"].pk}')
        page.get_by_role("button", name="View location").click()
        expect(page.locator("#balance-grid-table .tabulator-row")).to_have_count(3)
        assert not errors, errors
        browser.close()
