from datetime import date

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.inventory.models import GridSelection, UnitAsset
from apps.inventory.services.bulk_edit import bulk_edit_assets
from apps.inventory.services.receipts import receive_stock


@pytest.mark.django_db
def test_bulk_edit_updates_metadata_with_audit(administrator, unit_product, location_tree):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="BULK-EDIT-1",
    )
    asset = UnitAsset.objects.get(vendor_serial="BULK-EDIT-1")
    changed, transfer = bulk_edit_assets(
        user=administrator,
        asset_ids=[asset.pk],
        occurred_at=date.today(),
        updates={"supplier": "Updated supplier", "project_reference": "P-100"},
    )
    asset.refresh_from_db()
    assert changed == [asset]
    assert transfer is None
    assert asset.supplier == "Updated supplier"
    assert asset.project_reference == "P-100"
    assert AuditEvent.objects.filter(object_id=str(asset.pk), metadata__bulk_edit=True).exists()


@pytest.mark.django_db
def test_bulk_edit_rejects_asset_outside_scope(
    administrator, stock_manager_with_room_access, unit_product, other_location_tree
):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=other_location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="BULK-HIDDEN",
    )
    asset = UnitAsset.objects.get(vendor_serial="BULK-HIDDEN")
    with pytest.raises(PermissionDenied):
        bulk_edit_assets(
            user=stock_manager_with_room_access,
            asset_ids=[asset.pk],
            occurred_at=date.today(),
            updates={"notes": "not allowed"},
        )


@pytest.mark.django_db
def test_bulk_edit_view_uses_account_selection(client, administrator, unit_product, location_tree):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="BULK-VIEW",
    )
    asset = UnitAsset.objects.get(vendor_serial="BULK-VIEW")
    GridSelection.objects.create(
        user=administrator, grid_key="assets", selected_ids=[str(asset.pk)]
    )
    client.force_login(administrator)
    response = client.post(
        reverse("inventory:bulk_asset_edit"),
        {
            "occurred_at": date.today(),
            "change_invoice_number": "on",
            "invoice_number": "INV-200",
        },
    )
    assert response.status_code == 302
    asset.refresh_from_db()
    assert asset.invoice_number == "INV-200"
    assert GridSelection.objects.get(user=administrator, grid_key="assets").selected_ids == []
