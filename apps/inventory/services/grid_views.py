import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q

from apps.core.authorization import is_administrator

from ..models import GridPreference, SavedGridView

# Hard cap on the JSON blob's size — this is UI presentation state (column
# widths/order/visibility, filter values, sort, density), not user content;
# anything larger is either abuse or a client bug, not a real saved view.
MAX_STATE_BYTES = 20_000


def inventory_location_choices(user):
    """Only offer authorized rooms; retain inactive rooms for historical stock."""
    from apps.locations.models import Location, order_by_hierarchy
    from apps.locations.scoping import accessible_locations

    locations = list(
        order_by_hierarchy(accessible_locations(user).select_related("parent__parent"))
    )
    rooms = [
        [f"id:{room.pk}", f"{room.parent.name} / {room.name}"]
        for room in locations
        if room.level == Location.Level.STORAGE_ROOM
    ]
    countries = {}
    nodes = []
    shelves = []
    for location in locations:
        ancestors = [*location.ancestors(), location]
        country = next(node for node in ancestors if node.level == Location.Level.COUNTRY)
        room = next((node for node in ancestors if node.level == Location.Level.STORAGE_ROOM), None)
        countries[str(country.pk)] = country.name
        label = " / ".join(node.name for node in ancestors)
        nodes.append(
            {
                "id": str(location.pk),
                "country": f"id:{country.pk}",
                "room": f"id:{room.pk}" if room else "",
                "level": location.level,
                "label": f"{label} ({location.get_level_display()})",
            }
        )
        if location.level == Location.Level.RACK_SHELF:
            shelves.append([f"id:{location.pk}", label])
    return {
        "locations": locations,
        "room_filter_choices": rooms,
        "country_filter_choices": [[f"id:{pk}", name] for pk, name in countries.items()],
        "shelf_filter_choices": shelves,
        "location_filter_nodes": nodes,
    }


# The one place that knows which grids exist — SavedGridView.grid_key is a
# plain, unconstrained CharField precisely so a new grid (like "products",
# added here after templates/catalog/product_list.html was already calling
# it before this dict existed) never needs a migration, just one more entry.
VALID_GRID_KEYS = {
    SavedGridView.GRID_ASSETS: "Assets",
    SavedGridView.GRID_BALANCES: "Stock Balances",
    "products": "Products",
}


def list_saved_grid_views(*, user, grid_key):
    """Own views + anyone's shared views for this grid — the same
    "mine or shared, never someone else's private one" rule as
    apps.reporting.services' SavedReport listing.
    """
    return SavedGridView.objects.filter(grid_key=grid_key).filter(
        Q(created_by=user) | Q(is_shared=True)
    )


def pinned_grid_views(*, user):
    """Personal workspace shortcuts; shared views must be pinned by each user."""
    return SavedGridView.objects.filter(created_by=user, is_pinned=True).order_by(
        "grid_key", "name"
    )


def _validate_state(state):
    if not isinstance(state, dict):
        raise ValidationError("Invalid view state.")
    if len(json.dumps(state)) > MAX_STATE_BYTES:
        raise ValidationError("Saved view is too large.")


def create_saved_grid_view(*, user, name, grid_key, state, is_shared=False, is_default=False):
    name = name.strip()
    if not name:
        raise ValidationError("Name is required.")
    if grid_key not in VALID_GRID_KEYS:
        raise ValidationError("Unknown grid.")
    _validate_state(state)

    # Only an Administrator's saved views can be shared with everyone —
    # enforced here, not just hidden/disabled in the form.
    if is_shared and not is_administrator(user):
        is_shared = False

    with transaction.atomic():
        if is_default:
            _clear_existing_default(user=user, grid_key=grid_key)
        view = SavedGridView(
            name=name,
            grid_key=grid_key,
            state=state,
            is_shared=is_shared,
            is_default=is_default,
            created_by=user,
            updated_by=user,
        )
        view.full_clean()
        view.save()
    return view


def update_saved_grid_view(
    *, view, user, name=None, state=None, is_shared=None, is_default=None, is_pinned=None
):
    """Real rename/update, not delete-and-recreate — keeps the row's id (and
    therefore anyone else's reference to a *shared* view) stable across a
    rename, unlike the create-only flow this replaces for that case.
    """
    if view.created_by_id != user.id and not is_administrator(user):
        raise PermissionDenied("You can only update your own saved views.")

    if name is not None:
        name = name.strip()
        if not name:
            raise ValidationError("Name is required.")
        view.name = name
    if state is not None:
        _validate_state(state)
        view.state = state
    if is_shared is not None:
        view.is_shared = bool(is_shared) and is_administrator(user)
    if is_pinned is not None:
        # Pins are personal shortcuts. Administrators may maintain a shared
        # view, but they cannot pin it onto somebody else's workspace.
        if view.created_by_id != user.id:
            raise PermissionDenied("You can only pin your own saved views.")
        view.is_pinned = bool(is_pinned)

    with transaction.atomic():
        if is_default is True:
            _clear_existing_default(user=view.created_by, grid_key=view.grid_key, exclude=view)
            view.is_default = True
        elif is_default is False:
            view.is_default = False
        view.updated_by = user
        view.full_clean()
        view.save()
    return view


def grid_preference_for(*, user, grid_key):
    if grid_key not in VALID_GRID_KEYS:
        raise ValidationError("Unknown grid.")
    preference = GridPreference.objects.filter(user=user, grid_key=grid_key).first()
    return preference.state if preference else None


def save_grid_preference(*, user, grid_key, state):
    if grid_key not in VALID_GRID_KEYS:
        raise ValidationError("Unknown grid.")
    _validate_state(state)
    preference, _ = GridPreference.objects.update_or_create(
        user=user,
        grid_key=grid_key,
        defaults={"state": state},
    )
    return preference


def _clear_existing_default(*, user, grid_key, exclude=None):
    queryset = SavedGridView.objects.filter(created_by=user, grid_key=grid_key, is_default=True)
    if exclude is not None:
        queryset = queryset.exclude(pk=exclude.pk)
    queryset.update(is_default=False)


def delete_saved_grid_view(*, view, user):
    if view.created_by_id != user.id and not is_administrator(user):
        raise PermissionDenied("You can only delete your own saved views.")
    view.delete()
