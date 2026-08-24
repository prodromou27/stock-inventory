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
