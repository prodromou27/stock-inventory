from datetime import date
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.inventory.models import InventoryTransaction, UnitAsset, UnitStatus
from apps.inventory.services.corrections import reverse_transaction
from apps.inventory.services.internal_use import change_internal_use
from apps.inventory.services.receipts import receive_stock
from apps.inventory.services.reservations import reserve_stock

pytestmark = pytest.mark.django_db


@pytest.fixture
def internal_asset(administrator, unit_product, location_tree):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="INTERNAL-1",
    )
    return UnitAsset.objects.get(vendor_serial="INTERNAL-1")


def install(user, asset, **kwargs):
    data = dict(
        user=user,
        unit_asset_ids=[asset.pk],
        occurred_at=date.today(),
        notes="Installed in server room X",
    )
    data.update(kwargs)
    return change_internal_use(**data)


def test_install_return_and_scoped_visibility(
    client, stock_manager_with_room_access, internal_asset, location_tree
):
    user = stock_manager_with_room_access
    txn = install(user, internal_asset)
    internal_asset.refresh_from_db()
    assert internal_asset.status == UnitStatus.IN_USE
    assert internal_asset.current_location is None
    assert internal_asset.current_custody_transaction is None
    assert internal_asset.last_removal_date == date.today()
    assert "server room X" in internal_asset.notes
    assert txn.lines.get().from_location == location_tree["room"]
    client.force_login(user)
    grid = reverse("inventory:asset_grid_data")
    assert client.get(grid, {"status": "in_use"}).json()["total_count"] == 1
    assert client.get(grid, {"in_storage": "1"}).json()["total_count"] == 0
    assert (
        client.get(
            reverse("inventory:asset_list"), {"status": "in_use", "format": "csv"}
        ).status_code
        == 200
    )
    picker = client.get(
        reverse("inventory:asset_picker_data"), {"internal_use": "remove", "statuses": "in_use"}
    )
    assert picker.json()["total_count"] == 1
    install(
        user,
        internal_asset,
        returning=True,
        location=location_tree["room"],
        notes="Removed from installation",
    )
    internal_asset.refresh_from_db()
    assert internal_asset.status == UnitStatus.IN_STOCK
    assert internal_asset.current_location == location_tree["room"]
    assert internal_asset.status_history.count() == 3
    assert txn.lines.get().to_status == "in_use"


def test_invalid_transitions_and_missing_notes(administrator, internal_asset):
    with pytest.raises(ValidationError):
        install(administrator, internal_asset, notes=" ")
    install(administrator, internal_asset)
    with pytest.raises(ValidationError):
        install(administrator, internal_asset)
    with pytest.raises(ValidationError):
        install(administrator, internal_asset, returning=True)


def test_return_from_internal_use_allows_blank_notes(
    client, stock_manager_with_room_access, internal_asset, location_tree
):
    user = stock_manager_with_room_access
    install(user, internal_asset)
    internal_asset.refresh_from_db()
    original_notes = internal_asset.notes

    client.force_login(user)
    response = client.get(reverse("inventory:remove_from_use"))
    assert response.status_code == 200
    assert response.context["form"].fields["notes"].required is False
    assert response.context["form"].fields["notes"].label == "Return notes (optional)"
    response = client.post(
        reverse("inventory:remove_from_use"),
        {
            "unit_asset_ids": [str(internal_asset.pk)],
            "occurred_at": date.today(),
            "notes": "",
            "location": str(location_tree["room"].pk),
            "submission_token": response.context["form"].initial["submission_token"],
        },
    )
    assert response.status_code == 302
    txn = InventoryTransaction.objects.get(movement_type="remove_from_use")

    internal_asset.refresh_from_db()
    assert txn.notes == ""
    assert txn.lines.get().notes == ""
    assert internal_asset.status == UnitStatus.IN_STOCK
    assert internal_asset.current_location == location_tree["room"]
    assert internal_asset.notes == original_notes


def test_reservations_cannot_be_bypassed(administrator, internal_asset):
    reserve_stock(
        user=administrator,
        unit_asset_ids=[internal_asset.pk],
        occurred_at=date.today(),
        project_reference="PRJ",
        final_customer="Customer",
    )
    with pytest.raises(ValidationError):
        install(administrator, internal_asset)
    internal_asset.refresh_from_db()
    assert internal_asset.status == UnitStatus.RESERVED


def test_roles_scope_and_destination(
    client, stock_manager, read_only_user, administrator, internal_asset, other_location_tree
):
    for user in (stock_manager, read_only_user):
        with pytest.raises(PermissionDenied):
            install(user, internal_asset)
    client.force_login(read_only_user)
    assert client.post(reverse("inventory:put_in_use"), {}).status_code == 403


def test_atomic_rollback(administrator, internal_asset):
    count = InventoryTransaction.objects.count()
    with patch(
        "apps.inventory.services.internal_use.record_event",
        side_effect=RuntimeError("audit failure"),
    ):
        with pytest.raises(RuntimeError):
            install(administrator, internal_asset)
    internal_asset.refresh_from_db()
    assert internal_asset.status == UnitStatus.IN_STOCK
    assert InventoryTransaction.objects.count() == count


def test_mixed_customer_stock_rejects_entire_installation(
    administrator, internal_asset, unit_product, location_tree
):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="CUSTOMER-STOCK",
        stock_purpose="customer",
    )
    customer_asset = UnitAsset.objects.get(vendor_serial="CUSTOMER-STOCK")
    with pytest.raises(ValidationError):
        install(
            administrator, internal_asset, unit_asset_ids=[internal_asset.pk, customer_asset.pk]
        )
    internal_asset.refresh_from_db()
    assert internal_asset.status == UnitStatus.IN_STOCK


def test_return_destination_must_be_authorized(
    stock_manager_with_room_access, internal_asset, other_location_tree
):
    user = stock_manager_with_room_access
    install(user, internal_asset)
    with pytest.raises(PermissionDenied):
        install(user, internal_asset, returning=True, location=other_location_tree["room"])
    internal_asset.refresh_from_db()
    assert internal_asset.status == UnitStatus.IN_USE


def test_reversal_preserves_installation_history(administrator, internal_asset):
    txn = install(administrator, internal_asset)
    reverse_transaction(
        user=administrator,
        original_transaction=txn,
        occurred_at=date.today(),
        reason="Wrong selection",
    )
    internal_asset.refresh_from_db()
    assert internal_asset.status == UnitStatus.IN_STOCK
    assert txn.lines.get().to_status == "in_use"
    assert AuditEvent.objects.filter(
        object_id=str(txn.pk), event_type="movement_completed"
    ).exists()


def test_web_post_is_not_applied_twice(client, stock_manager_with_room_access, internal_asset):
    client.force_login(stock_manager_with_room_access)
    url = reverse("inventory:put_in_use")
    response = client.get(url)
    assert response.status_code == 200
    data = {
        "unit_asset_ids": [str(internal_asset.pk)],
        "occurred_at": date.today(),
        "notes": "Server room X",
        "submission_token": response.context["form"].initial["submission_token"],
    }
    assert client.post(url, data).status_code == 302
    client.post(url, data)
    assert (
        internal_asset.transaction_lines.filter(transaction__movement_type="put_in_use").count()
        == 1
    )
