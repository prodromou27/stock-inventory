from datetime import date
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.catalog.models import Product
from apps.inventory.models import UnitAsset
from apps.inventory.services.asset_editing import edit_asset
from apps.inventory.services.receipts import DuplicateSerialError, receive_stock

pytestmark = pytest.mark.django_db


@pytest.fixture
def asset(administrator, unit_product, location_tree):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="EDIT-ME",
    )
    return UnitAsset.objects.get(vendor_serial="EDIT-ME")


def edit(user, asset, **kwargs):
    data = dict(
        user=user,
        pk=asset.pk,
        values={"name": "Office firewall", "vendor_serial": ""},
        brand_name=asset.product.brand.name,
        model=asset.product.model,
        sku=asset.product.sku,
    )
    data.update(kwargs)
    return edit_asset(**data)


def test_brand_model_correction_is_individual_and_preserves_snapshot(
    stock_manager_with_room_access, asset, unit_product
):
    before = list(asset.transaction_lines.values())
    updated = edit(
        stock_manager_with_room_access, asset, brand_name="Correct brand", model="Correct model"
    )
    assert updated.product_id != unit_product.pk
    unit_product.refresh_from_db()
    assert unit_product.model == "FG-100F"
    assert list(asset.transaction_lines.values()) == before
    assert updated.name == "Office firewall"
    assert updated.normalized_serial == ""
    audit = AuditEvent.objects.filter(object_id=str(asset.pk), event_type="record_updated").latest(
        "occurred_at"
    )
    assert audit.old_values["model"] == "FG-100F"
    assert audit.new_values["model"] == "Correct model"


def test_serial_duplicate_requires_acknowledgement(
    administrator, asset, unit_product, location_tree
):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="DUPLICATE",
    )
    with pytest.raises(DuplicateSerialError):
        edit(administrator, asset, values={"vendor_serial": "duplicate"})
    edit(
        administrator,
        asset,
        values={"vendor_serial": "duplicate"},
        duplicate_serial_acknowledged=True,
    )
    assert AuditEvent.objects.filter(
        object_id=str(asset.pk), event_type="duplicate_serial_acknowledged"
    ).exists()


def test_edit_and_catalog_creation_roll_back_when_audit_fails(administrator, asset):
    count = Product.objects.count()
    with patch(
        "apps.inventory.services.asset_editing.record_event",
        side_effect=RuntimeError("audit unavailable"),
    ):
        with pytest.raises(RuntimeError):
            edit(administrator, asset, model="A different model")
    asset.refresh_from_db()
    assert asset.vendor_serial == "EDIT-ME"
    assert Product.objects.count() == count


def test_forbidden_and_lifecycle_edits_are_rejected(read_only_user, administrator, asset):
    with pytest.raises(PermissionDenied):
        edit(read_only_user, asset)
    with pytest.raises(ValidationError):
        edit(administrator, asset, values={"current_location": None})


def test_edit_form_and_permission_checks(
    client, stock_manager_with_room_access, read_only_user, asset
):
    url = reverse("inventory:asset_edit", args=[asset.pk])
    client.force_login(stock_manager_with_room_access)
    assert client.get(url).status_code == 200
    response = client.post(
        url,
        {
            "name": "Front desk",
            "brand_name": asset.product.brand.name,
            "model": asset.product.model,
            "sku": asset.product.sku,
        },
    )
    assert response.status_code == 302
    asset.refresh_from_db()
    assert asset.name == "Front desk"
    assert asset.vendor_serial == ""
    client.force_login(read_only_user)
    assert client.get(url).status_code == 403
    assert client.post(url, {}).status_code == 403


def test_out_of_scope_manager_cannot_edit(client, stock_manager, asset):
    client.force_login(stock_manager)
    url = reverse("inventory:asset_edit", args=[asset.pk])
    assert client.get(url).status_code == 404
    assert client.post(url, {}).status_code == 404
    with pytest.raises(PermissionDenied):
        edit(stock_manager, asset)


def test_asset_name_is_searchable(client, stock_manager_with_room_access, asset):
    edit(stock_manager_with_room_access, asset)
    client.force_login(stock_manager_with_room_access)
    url = reverse("inventory:asset_grid_data")
    assert client.get(url, {"q": "Office firewall"}).json()["total_count"] == 1
    assert client.get(url, {"name": "Office"}).json()["data"][0]["name"] == "Office firewall"
