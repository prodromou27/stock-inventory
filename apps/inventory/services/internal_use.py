"""Internal installation is distinct from employee/customer custody."""

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role
from apps.locations.scoping import require_location_access, require_room_or_below

from ..access import require_asset_access, scope_asset_queryset
from ..models import MovementType, StockPurpose, UnitAsset, UnitStatus
from .ledger import create_transaction_header, write_unit_line


def eligible_internal_use_assets(*, user, returning=False):
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    assets = scope_asset_queryset(
        user,
        UnitAsset.objects.select_related(
            "product__brand", "product__product_type", "current_location"
        ),
    )
    if returning:
        return assets.filter(status=UnitStatus.IN_USE)
    return assets.filter(
        status=UnitStatus.IN_STOCK, stock_purpose=StockPurpose.INTERNAL, installed_in=None
    )


@transaction.atomic
def change_internal_use(
    *, user, unit_asset_ids, occurred_at, notes, returning=False, location=None
):
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    notes = notes.strip()
    if not notes:
        raise ValidationError("Enter installation details or removal notes.")
    ids = set(unit_asset_ids or [])
    if not ids:
        raise ValidationError("Select at least one asset.")
    if returning:
        if location is None:
            raise ValidationError("Select a receiving storage room.")
        require_location_access(user, location)
        require_room_or_below(location)
        if not location.is_active:
            raise ValidationError("Choose an active receiving location.")
    elif location is not None:
        raise ValidationError("Installation details belong in notes, not a stock-room location.")
    assets = list(UnitAsset.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
    if len(assets) != len(ids):
        raise ValidationError("One or more assets could not be found.")
    expected = UnitStatus.IN_USE if returning else UnitStatus.IN_STOCK
    for asset in assets:
        require_asset_access(user, asset)
        if asset.status != expected:
            raise ValidationError(f"Only {expected.label} assets are eligible for this action.")
        if asset.installed_in_id:
            raise ValidationError(
                "Remove installed components from their parent before this action."
            )
        if not returning and (
            asset.stock_purpose != StockPurpose.INTERNAL or not asset.current_location_id
        ):
            raise ValidationError("Only Internal assets in a stock room can be put in use.")
    txn = create_transaction_header(
        movement_type=MovementType.REMOVE_FROM_USE if returning else MovementType.PUT_IN_USE,
        performed_by=user,
        occurred_at=occurred_at,
        notes=notes,
        destination_location=location if returning else None,
    )
    for number, asset in enumerate(assets, 1):
        old_notes = asset.notes
        asset.notes = "\n".join(
            filter(
                None,
                [
                    old_notes,
                    f"{occurred_at}: {'Removed from use' if returning else 'In use'} — {notes}",
                ],
            )
        )
        write_unit_line(
            transaction=txn,
            line_number=number,
            asset=asset,
            to_status=UnitStatus.IN_STOCK if returning else UnitStatus.IN_USE,
            to_location=location if returning else None,
            user=user,
            notes=notes,
        )
        record_event(
            actor=user,
            event_type=AuditEvent.EventType.RECORD_UPDATED,
            obj=asset,
            summary="Recorded internal installation/removal notes",
            old_values={"notes": old_notes},
            new_values={"notes": asset.notes},
        )
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.MOVEMENT_COMPLETED,
        obj=txn,
        summary=f"{txn.get_movement_type_display()}: {len(assets)} asset(s)",
    )
    return txn
