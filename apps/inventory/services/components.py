"""Component install/remove — associates a Component-category UnitAsset with
a parent UnitAsset it's physically installed inside (e.g. a RAM stick into a
laptop), without moving stock in the ledger sense: the component's status
never changes and neither does its on-hand accounting, only its `installed_in`
pointer and (if the parent is elsewhere) its location. Mirrors
apps.inventory.services.transfers.bulk_transfer()'s same-status write_unit_line
call (to_status=asset.status, only to_location changes) rather than
assignments.py's status-changing pattern, since nothing about install/remove
changes what UnitStatus the component is in.

Deliberately conservative scope: both the component and the parent must
currently be In Stock (not Assigned/Delivered/Reserved/etc.) — this keeps
authorization simple (an Assigned/Delivered asset has no `current_location`
to check access against) and avoids inventing custody semantics nobody asked
for. Installing a component into equipment that's already out in the field is
out of scope for this phase.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.catalog.models import ItemCategory
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role
from apps.locations.scoping import require_location_access

from ..models import MovementType, UnitAsset, UnitStatus
from .ledger import create_transaction_header, write_unit_line


def _validate_no_cycle(component, parent):
    """A component can't end up installed, however indirectly, inside
    itself. Walks the parent's own installed_in chain (bounded by `seen`
    against a corrupt/looping chain slipping in some other way).
    """
    node = parent
    seen = set()
    while node is not None:
        if node.pk == component.pk:
            raise ValidationError(
                "Can't install this component there — it would end up installed inside itself."
            )
        if node.pk in seen:
            break
        seen.add(node.pk)
        node = node.installed_in


@transaction.atomic
def install_component(*, user, component_id, parent_id, occurred_at, notes=""):
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)
    if str(component_id) == str(parent_id):
        raise ValidationError("An asset cannot be installed into itself.")

    # No select_related("installed_in") here — installed_in is nullable, and
    # PostgreSQL rejects FOR UPDATE across an outer join. _validate_no_cycle()
    # below walks that chain lazily instead (a handful of extra queries at
    # most, for what's expected to be a shallow chain).
    assets = list(
        UnitAsset.objects.select_for_update()
        .select_related("product")
        .filter(pk__in=[component_id, parent_id])
    )
    by_pk = {str(asset.pk): asset for asset in assets}
    component = by_pk.get(str(component_id))
    parent = by_pk.get(str(parent_id))
    if component is None or parent is None:
        raise ValidationError("One or both assets could not be found.")

    if component.product.category != ItemCategory.COMPONENT:
        raise ValidationError("Only Component-category items can be installed into another asset.")
    if component.installed_in_id is not None:
        raise ValidationError(
            "This component is already installed in another asset — remove it from there first."
        )
    if component.status != UnitStatus.IN_STOCK:
        raise ValidationError("The component must be In Stock to install it.")
    if parent.status != UnitStatus.IN_STOCK:
        raise ValidationError("The target asset must be In Stock to install a component into it.")
    _validate_no_cycle(component, parent)

    require_location_access(user, component.current_location)
    require_location_access(user, parent.current_location)

    txn = create_transaction_header(
        movement_type=MovementType.INSTALL_COMPONENT,
        performed_by=user,
        occurred_at=occurred_at,
        destination_location=parent.current_location,
        notes=notes,
    )

    component.installed_in = parent
    write_unit_line(
        transaction=txn,
        line_number=1,
        asset=component,
        to_status=component.status,
        to_location=parent.current_location,
        user=user,
        notes=notes,
    )

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.COMPONENT_ASSOCIATION_CHANGED,
        obj=component,
        summary=f"Installed {component} into {parent}",
        old_values={"installed_in": None},
        new_values={"installed_in": str(parent.pk)},
    )
    return txn


@transaction.atomic
def remove_component(*, user, component_id, occurred_at, notes=""):
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)

    # Same outer-join-vs-FOR-UPDATE restriction as install_component() above
    # — no select_related("installed_in") on a locked queryset.
    component = (
        UnitAsset.objects.select_for_update()
        .select_related("product")
        .filter(pk=component_id)
        .first()
    )
    if component is None:
        raise ValidationError("Component could not be found.")
    if component.installed_in_id is None:
        raise ValidationError("This component isn't currently installed in anything.")

    parent = component.installed_in
    require_location_access(user, component.current_location)

    txn = create_transaction_header(
        movement_type=MovementType.REMOVE_COMPONENT,
        performed_by=user,
        occurred_at=occurred_at,
        destination_location=component.current_location,
        notes=notes,
    )

    component.installed_in = None
    write_unit_line(
        transaction=txn,
        line_number=1,
        asset=component,
        to_status=component.status,
        to_location=component.current_location,
        user=user,
        notes=notes,
    )

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.COMPONENT_ASSOCIATION_CHANGED,
        obj=component,
        summary=f"Removed {component} from {parent}",
        old_values={"installed_in": str(parent.pk)},
        new_values={"installed_in": None},
    )
    return txn
