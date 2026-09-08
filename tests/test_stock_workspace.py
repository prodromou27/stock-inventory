from datetime import date
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.inventory.models import UnitAsset
from apps.inventory.services.assignments import deliver_to_customer
from apps.inventory.services.receipts import receive_stock

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("page", ["asset_list", "balance_list"])
def test_room_dropdown_is_scoped(
    client, stock_manager, stock_manager_with_room_access, location_tree, page
):
    client.force_login(stock_manager_with_room_access)
    response = client.get(reverse(f"inventory:{page}"))
    assert [f"id:{location_tree['room'].pk}", "Wonderland / Room A"] in response.context[
        "room_filter_choices"
    ]
    from apps.accounts.models import UserLocationAccess

    UserLocationAccess.objects.filter(user=stock_manager).delete()
    client.force_login(stock_manager)
    assert client.get(reverse(f"inventory:{page}")).context["room_filter_choices"] == []


def test_room_dropdown_matches_exact_room_and_rejects_invalid_id(
    client, stock_manager_with_room_access, room_asset, location_tree
):
    client.force_login(stock_manager_with_room_access)
    url = reverse("inventory:asset_grid_data")
    assert (
        client.get(url, {"storage_room": f"id:{location_tree['room'].pk}"}).json()["total_count"]
        == 1
    )
    assert client.get(url, {"storage_room": "id:invalid"}).json()["total_count"] == 0
    assert (
        client.get(url, {"storage_room": f"id:{location_tree['country'].pk}"}).json()["total_count"]
        == 0
    )


@pytest.fixture
def room_asset(administrator, unit_product, location_tree):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="WORKSPACE-1",
        condition="new",
    )
    return UnitAsset.objects.get(vendor_serial="WORKSPACE-1")


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("product_type", "Firewall", 1),
        ("product_type", "Server", 0),
        ("type", "Firewall", 1),
        ("country", "Wonderland", 1),
        ("country", "Elsewhere", 0),
        ("storage_room", "Room A", 1),
        ("storage_room", "Missing", 0),
        ("condition", "new", 1),
        ("condition", "damaged", 0),
    ],
)
def test_grid_filters_match_visible_columns(
    client, stock_manager_with_room_access, room_asset, key, value, expected
):
    client.force_login(stock_manager_with_room_access)
    response = client.get(reverse("inventory:asset_grid_data"), {key: value})
    assert response.status_code == 200
    assert response.json()["total_count"] == expected


def test_room_stock_excludes_delivered_but_history_remains_visible(
    client, stock_manager_with_room_access, room_asset
):
    manager = stock_manager_with_room_access
    deliver_to_customer(
        user=manager,
        final_customer="Client",
        occurred_at=date.today(),
        unit_asset_ids=[room_asset.pk],
    )
    client.force_login(manager)
    assert (
        client.get(reverse("inventory:asset_grid_data"), {"in_storage": "1"}).json()["total_count"]
        == 0
    )
    assert (
        client.get(reverse("inventory:asset_grid_data"), {"status": "delivered"}).json()[
            "total_count"
        ]
        == 1
    )


def test_named_location_filter_cannot_expand_access(client, stock_manager, room_asset):
    client.force_login(stock_manager)
    assert (
        client.get(reverse("inventory:asset_grid_data"), {"country": "Wonderland"}).json()[
            "total_count"
        ]
        == 0
    )


def test_room_filter_includes_items_on_shelves(client, administrator, unit_product, location_tree):
    from apps.locations.models import Location
    from apps.locations.services import create_location

    shelf = create_location(
        user=administrator,
        name="Shelf 9",
        level=Location.Level.RACK_SHELF,
        parent=location_tree["room"],
    )
    receive_stock(
        user=administrator,
        product=unit_product,
        location=shelf,
        occurred_at=date.today(),
        vendor_serial="SHELF-ITEM",
    )
    client.force_login(administrator)
    response = client.get(
        reverse("inventory:asset_grid_data"),
        {"country": "Wonderland", "storage_room": "Room A", "shelf": "Shelf 9"},
    )
    assert response.json()["total_count"] == 1


def test_quantity_stock_filters_match_type_and_room(
    client, administrator, quantity_product, location_tree
):
    receive_stock(
        user=administrator,
        product=quantity_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        quantity=5,
    )
    client.force_login(administrator)
    url = reverse("inventory:balance_grid_data")
    assert (
        client.get(url, {"product_type": "Toner", "storage_room": "Room A"}).json()["total_count"]
        == 1
    )
    assert client.get(url, {"product_type": "Server"}).json()["total_count"] == 0


@pytest.mark.parametrize("pdf_fails", [False, True])
def test_complete_delivery_generates_document_without_reissuing_on_pdf_failure(
    client, stock_manager_with_room_access, room_asset, pdf_fails
):
    client.force_login(stock_manager_with_room_access)
    with patch("apps.documents.services.generate_document") as generate:
        if pdf_fails:
            generate.side_effect = ValidationError("Storage unavailable")
        else:
            generate.return_value.get_absolute_url.return_value = "/documents/test-document/"
        response = client.post(
            reverse("inventory:deliver"),
            {
                "final_customer": "Client",
                "project_reference": "PROJECT-42",
                "occurred_at": date.today().isoformat(),
                "unit_asset_ids": [str(room_asset.pk)],
                "generate_document": "1",
            },
        )
    assert response.status_code == 302
    generate.assert_called_once()
    room_asset.refresh_from_db()
    assert room_asset.status == "delivered"
    assert room_asset.current_location is None
    assert room_asset.project_reference == "PROJECT-42"
    assert room_asset.transaction_lines.filter(transaction__movement_type="delivery").count() == 1
    if pdf_fails:
        assert response.url == room_asset.current_custody_transaction.get_absolute_url()
    else:
        assert response.url == "/documents/test-document/"
