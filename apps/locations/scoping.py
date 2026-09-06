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

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q

from apps.core.authorization import is_administrator


def granted_location_paths(user):
    """The `path` of every Location directly granted to `user` (not
    expanded to descendants — there are only ever a handful of these per
    user, unlike accessible_locations()'s full expanded set, which is why
    apps.inventory.access uses this directly for its own multi-field OR
    query rather than iterating every accessible Location).

    Deliberately uncached, recomputed fresh on every call: a location-access
    grant/revoke must take effect on the very next call, not just the next
    request (tests/test_scoping.py::
    test_revoking_country_access_immediately_blocks_all_descendants tests
    exactly this and documents the same guarantee). A per-instance or
    per-request memoization scheme was considered for the N+1 this causes
    in per-asset loops (apps.inventory.access.require_asset_access /
    require_location_access, called once per asset in every movement
    service) but rejected: within a single service call an already-fetched
    `user` object can have its access revoked and re-checked against
    (exactly what that test does), and any cache scoped to that object's
    lifetime would serve the stale answer.
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


def require_room_or_below(location):
    """Raises ValidationError unless `location` is a Storage Room, Rack/
    Cabinet, or Shelf/Bin — a Country/Site/Floor is an authorization
    boundary and a tree parent, never a valid place to actually hold stock.
    `location=None` passes (a field that's optional at this layer, e.g. an
    admin-correction leaving a location unset, is a different concern from
    "the level chosen is too high"); callers that require a location at all
    enforce that separately.
    """
    from .models import LocationLevel

    if location is None:
        return
    if location.level in (LocationLevel.COUNTRY, LocationLevel.SITE, LocationLevel.FLOOR):
        raise ValidationError(
            f"'{location}' is a {location.get_level_display()}, not a storage location. "
            "Select a Storage Room (or a Rack/Shelf within one)."
        )


def country_for_location(location):
    """Walks up to `location`'s Country-level ancestor (or returns it
    directly if it already is one). Used wherever a country/legal-entity
    scope needs deriving from a specific stock location — e.g. apps.documents.
    services.generate_document() resolving which CountryBrandingProfile
    applies to a transaction. Returns None for `location=None` or an
    orphaned/mid-migration node with no Country ancestor.
    """
    from .models import LocationLevel

    if location is None:
        return None
    if location.level == LocationLevel.COUNTRY:
        return location
    for ancestor in location.ancestors():
        if ancestor.level == LocationLevel.COUNTRY:
            return ancestor
    return None


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
