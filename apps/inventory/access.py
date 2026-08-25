"""Location-scope access checks for InventoryTransaction and anything keyed
off one (documents, attachments — Phase 5).

Only receipt, transfer, and return set a location on the transaction
*header* (source_location/destination_location) — every other movement type
(assignment, delivery, reservation, reservation release, mark damaged/lost,
disposal, correction, reversal) only carries location information on its
*lines* (InventoryTransactionLine.from_location/to_location). A scope check
that only looked at the header would let any authenticated user view any
assignment/delivery/reservation/disposal transaction regardless of scope —
this module is the one place that check happens, so every caller (the
transaction detail view, and the document/attachment download views built
on top of it) gets it right.
"""

from django.core.exceptions import PermissionDenied
from django.db.models import Exists, OuterRef, Q

from apps.core.authorization import is_administrator
from apps.locations.models import Location
from apps.locations.scoping import granted_location_paths, require_location_access


def transaction_locations(txn):
    """Every distinct Location referenced by `txn`, header or line level."""
    location_ids = set()
    if txn.source_location_id:
        location_ids.add(txn.source_location_id)
    if txn.destination_location_id:
        location_ids.add(txn.destination_location_id)

    line_location_ids = txn.lines.values_list("from_location_id", "to_location_id")
    for from_id, to_id in line_location_ids:
        if from_id:
            location_ids.add(from_id)
        if to_id:
            location_ids.add(to_id)

    return Location.objects.filter(pk__in=location_ids)


def require_transaction_access(user, txn):
    """Require access to every location referenced by the transaction."""
    locations = list(transaction_locations(txn))
    if not locations:
        # No location information at all on this transaction (shouldn't
        # normally happen — every movement type touches at least one line
        # location) — fail open only in that unexpected case rather than
        # hide a transaction no location check can be run against.
        raise PermissionDenied("This transaction has no authorization scope.")

    for location in locations:
        require_location_access(user, location)


def require_asset_access(user, asset):
    """Authorize an asset even after it has physically left storage."""
    if asset.current_location_id:
        require_location_access(user, asset.current_location)
        return

    last_location_id = (
        asset.transaction_lines.exclude(from_location=None)
        .order_by("-transaction__created_at", "-line_number")
        .values_list("from_location_id", flat=True)
        .first()
    )
    if not last_location_id:
        raise PermissionDenied("This asset has no authorization scope.")
    require_location_access(user, Location.objects.get(pk=last_location_id))


def scope_transaction_queryset(user, queryset):
    """The list-view equivalent of require_transaction_access() — filters a
    queryset of InventoryTransaction to rows touching (header *or* line
    level) a location the user can access. Used by the "Transactions and
    documents" screen (spec §14) and by reporting queries built on
    InventoryTransaction.

    Built from the user's *granted* paths directly (not the fully expanded
    accessible_locations() set) to keep the OR'd clause list small — a user
    typically has only a handful of grants.
    """
    if is_administrator(user):
        return queryset.distinct()

    paths = granted_location_paths(user)
    if not paths:
        return queryset.none()

    query = Q()
    for path in paths:
        query |= Q(source_location__path__descendant_or_self=path)
        query |= Q(destination_location__path__descendant_or_self=path)
        query |= Q(lines__from_location__path__descendant_or_self=path)
        query |= Q(lines__to_location__path__descendant_or_self=path)
    visible = queryset.filter(query).distinct()
    accessible_location_query = Q()
    for path in paths:
        accessible_location_query |= Q(path__descendant_or_self=path)
    accessible_ids = Location.objects.filter(accessible_location_query)

    from .models import InventoryTransactionLine

    unauthorized_lines = InventoryTransactionLine.objects.filter(
        transaction_id=OuterRef("pk")
    ).filter(
        (Q(from_location__isnull=False) & ~Q(from_location__in=accessible_ids))
        | (Q(to_location__isnull=False) & ~Q(to_location__in=accessible_ids))
    )
    return (
        visible.exclude(Q(source_location__isnull=False) & ~Q(source_location__in=accessible_ids))
        .exclude(
            Q(destination_location__isnull=False) & ~Q(destination_location__in=accessible_ids)
        )
        .annotate(has_unauthorized_lines=Exists(unauthorized_lines))
        .filter(has_unauthorized_lines=False)
    )


def scope_asset_status_history_queryset(user, queryset):
    """Same idea as scope_transaction_queryset() but for AssetStatusHistory
    (the "Complete asset movement history" report, spec §15) — scoped via
    its own from_location/to_location rather than joining back to a
    transaction's lines.
    """
    if is_administrator(user):
        return queryset

    paths = granted_location_paths(user)
    if not paths:
        return queryset.none()

    query = Q()
    for path in paths:
        query |= Q(from_location__path__descendant_or_self=path)
        query |= Q(to_location__path__descendant_or_self=path)
    return queryset.filter(query)
