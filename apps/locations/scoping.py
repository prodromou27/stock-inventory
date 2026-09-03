"""The centralized authorization-scope layer described in
docs/architecture/04-permission-matrix.md (there attributed to `core.scoping`;
moved here during implementation since it necessarily depends on Location and
UserLocationAccess, and `core` is meant to stay dependency-free — see
docs/architecture/01-repository-structure.md's app dependency table).

Every view/service that reads or writes location-scoped data (locations
themselves now; inventory, transactions, reports, attachments from Phase 3
onward) must go through accessible_locations()/scope_queryset()/
require_location_access() — never query those models directly.
"""

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from apps.core.authorization import is_administrator


def granted_location_paths(user):
    """The `path` of every Location directly granted to `user` (not
    expanded to descendants — there are only ever a handful of these per
    user, unlike accessible_locations()'s full expanded set, which is why
    apps.inventory.access uses this directly for its own multi-field OR
    query rather than iterating every accessible Location).
    """
    from apps.accounts.models import UserLocationAccess

    return list(
        UserLocationAccess.objects.filter(user=user).values_list("location__path", flat=True)
    )


def accessible_locations(user):
    """Every Location the user may see: all of them for an Administrator,
    otherwise every granted node and its descendants.
    """
    return scope_queryset(user, _location_queryset(), location_field=None)


def scope_queryset(user, queryset, location_field=None):
    """Filters `queryset` to rows under a location the user has access to.

    `location_field` is the (optionally dotted) relation from `queryset`'s
    model to a Location — e.g. "current_location" for UnitAsset (Phase 3+).
    Pass None (or "") when `queryset`'s model *is* Location itself.
    """
    if is_administrator(user):
        return queryset

    paths = granted_location_paths(user)
    if not paths:
        return queryset.none()

    prefix = f"{location_field}__" if location_field else ""
    query = Q()
    for path in paths:
        query |= Q(**{f"{prefix}path__descendant_or_self": path})
    return queryset.filter(query)


def require_location_access(user, location):
    """Raises PermissionDenied unless `user` may access `location` (or
    `location` is None, e.g. an item with no assigned location yet).
    """
    if location is None or is_administrator(user):
        return

    location_path = str(location.path)
    for granted_path in granted_location_paths(user):
        if location_path == granted_path or location_path.startswith(f"{granted_path}."):
            return

    raise PermissionDenied("You do not have access to this location.")


def _location_queryset():
    from .models import Location

    return Location.objects.all()


def location_breadcrumb_map():
    """{location_id: {"country": name, "storage_room": name, "shelf": name}}
    for every Location — used by apps.inventory's grid JSON endpoints to show
    country/storage-room/shelf columns without an N+1 walk of
    Location.ancestors() per row (that method is a per-object parent-chain
    walk, fine for a single detail page, wrong inside a loop over a paginated
    grid of rows).

    Deliberately built from the *whole* Location table, not
    accessible_locations(user): a Stock Manager's grant can start below the
    Country level (e.g. a single Storage Room), so the Country/Site rows
    above that grant point fall outside their accessible set even though the
    name is legitimate read-only context for an asset they ARE authorized to
    see (it says where the room is, not access to any other room in that
    country). The Location table is small — an organizational tree, not
    asset-count-sized — so one unfiltered query here is cheap regardless of
    how many rows a grid page renders.
    """
    from .models import Location, LocationLevel

    nodes = list(Location.objects.only("id", "parent_id", "level", "name"))
    node_by_id = {node.id: node for node in nodes}

    breadcrumbs = {}
    for node in nodes:
        breadcrumb = {"country": "", "storage_room": "", "shelf": ""}
        current = node
        while current is not None:
            if current.level == LocationLevel.COUNTRY:
                breadcrumb["country"] = current.name
            elif current.level == LocationLevel.STORAGE_ROOM:
                breadcrumb["storage_room"] = current.name
            elif current.level == LocationLevel.SHELF_BIN:
                breadcrumb["shelf"] = current.name
            current = node_by_id.get(current.parent_id)
        breadcrumbs[node.id] = breadcrumb
    return breadcrumbs
