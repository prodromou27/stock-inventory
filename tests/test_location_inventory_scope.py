from datetime import date

import pytest
from django.urls import reverse

from apps.inventory.services.grid_views import inventory_location_choices
from apps.inventory.services.receipts import receive_stock
from apps.locations.models import Location
from apps.locations.services import create_location

pytestmark = pytest.mark.django_db


@pytest.fixture
def stocked_hierarchy(administrator, location_tree, unit_product, quantity_product):
    room = location_tree["room"]
    shelf = create_location(
        user=administrator, level=Location.Level.RACK_SHELF, name="A", parent=room
    )
    other = create_location(
        user=administrator,
        level=Location.Level.STORAGE_ROOM,
        name="Lab",
        parent=location_tree["country"],
    )
    for node, serial in [(room, "ROOM"), (shelf, "SHELF"), (other, "LAB")]:
        receive_stock(
            user=administrator,
            product=unit_product,
            location=node,
            occurred_at=date.today(),
            vendor_serial=serial,
        )
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=node,
            occurred_at=date.today(),
            quantity=5,
        )
    return room, shelf, other


@pytest.mark.parametrize("endpoint", ["asset_grid_data", "balance_grid_data"])
def test_parent_selection_includes_descendants(
    client, administrator, location_tree, stocked_hierarchy, endpoint
):
    room, shelf, _ = stocked_hierarchy
    client.force_login(administrator)
    for params, expected in [
        ({"country": f'id:{location_tree["country"].pk}'}, 3),
        ({"storage_room": f"id:{room.pk}"}, 2),
        ({"shelf": f"id:{shelf.pk}"}, 1),
        ({"location": str(room.pk)}, 2),
    ]:
        response = client.get(reverse(f"inventory:{endpoint}"), params)
        assert response.status_code == 200
        assert response.json()["total_count"] == expected


def test_country_filter_does_not_expand_room_permission(
    client, stock_manager_with_room_access, location_tree, stocked_hierarchy
):
    client.force_login(stock_manager_with_room_access)
    response = client.get(
        reverse("inventory:asset_grid_data"), {"country": f'id:{location_tree["country"].pk}'}
    )
    assert {row["serial"] for row in response.json()["data"]} == {"ROOM", "SHELF"}
    response = client.get(
        reverse("inventory:asset_list"),
        {"country": f'id:{location_tree["country"].pk}', "format": "csv"},
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert "SHELF" in content and "LAB" not in content


def test_location_options_derive_country_without_exposing_siblings(
    stock_manager_with_room_access, location_tree, stocked_hierarchy
):
    room, shelf, other = stocked_hierarchy
    context = inventory_location_choices(stock_manager_with_room_access)
    nodes = {node["id"]: node for node in context["location_filter_nodes"]}
    assert str(other.pk) not in nodes
    assert nodes[str(shelf.pk)]["country"] == f'id:{location_tree["country"].pk}'
    assert nodes[str(shelf.pk)]["room"] == f"id:{room.pk}"
    assert nodes[str(shelf.pk)]["label"].endswith("A (Shelf/Rack)")


def test_invalid_location_filter_returns_empty_not_500(client, administrator):
    client.force_login(administrator)
    for key in ("country", "storage_room", "shelf", "location"):
        response = client.get(reverse("inventory:asset_grid_data"), {key: "id:invalid"})
        assert response.status_code == 200
        assert response.json()["total_count"] == 0
