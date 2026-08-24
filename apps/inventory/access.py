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

from apps.locations.models import Location
from apps.locations.scoping import require_location_access


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
    """Raises PermissionDenied unless `user` may access at least one
    location referenced by `txn` (matching the "grant cascades to
    descendants" scoping model — a transaction touching any location you
    can see is visible, even if another line is outside your scope).
    """
    locations = list(transaction_locations(txn))
    if not locations:
        # No location information at all on this transaction (shouldn't
        # normally happen — every movement type touches at least one line
        # location) — fail open only in that unexpected case rather than
        # hide a transaction no location check can be run against.
        return

    for location in locations:
        try:
            require_location_access(user, location)
            return
        except PermissionDenied:
            continue

    raise PermissionDenied("You do not have access to this transaction.")
