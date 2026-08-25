"""The status-transition table from docs/architecture/03-status-and-movement-rules.md,
made checkable. Every movement service validates every unit line against this
before writing anything (Administrator corrections/reversals are exempt by
design — they call write_unit_line directly, bypassing this check).

Transfer is deliberately NOT modeled here as a same-status "transition"
(there is no IN_STOCK -> IN_STOCK / RESERVED -> RESERVED entry below) —
services/transfers.py checks eligibility directly
(`asset.status in TRANSFERABLE_STATUSES`). A same-status self-loop would be
indistinguishable from "reserve an already-reserved asset" or "assess an
asset that was never returned" to this table, since both of those also
compare a status against itself.
"""

from django.core.exceptions import ValidationError

from .models import UnitStatus

TRANSFERABLE_STATUSES = {UnitStatus.IN_STOCK, UnitStatus.RESERVED}

VALID_UNIT_TRANSITIONS = {
    UnitStatus.IN_STOCK: {
        UnitStatus.RESERVED,
        UnitStatus.ASSIGNED,
        UnitStatus.DELIVERED,
        UnitStatus.DAMAGED,
        UnitStatus.LOST,
        UnitStatus.DISPOSED,
    },
    UnitStatus.RESERVED: {
        UnitStatus.IN_STOCK,
        UnitStatus.ASSIGNED,
        UnitStatus.DELIVERED,
        UnitStatus.LOST,
        UnitStatus.DISPOSED,
    },
    UnitStatus.ASSIGNED: {
        UnitStatus.RETURNED,
        UnitStatus.LOST,
        UnitStatus.DAMAGED,
        UnitStatus.DISPOSED,
    },
    UnitStatus.DELIVERED: {
        UnitStatus.RETURNED,
    },
    UnitStatus.RETURNED: {
        UnitStatus.IN_STOCK,
        UnitStatus.DAMAGED,
        UnitStatus.DISPOSED,
    },
    UnitStatus.DAMAGED: {
        UnitStatus.IN_STOCK,
        UnitStatus.DISPOSED,
    },
    UnitStatus.LOST: set(),  # recovery only via Administrator correction
    UnitStatus.DISPOSED: set(),  # terminal except an Administrator reversal
}


def validate_unit_transition(from_status, to_status):
    allowed = VALID_UNIT_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise ValidationError(
            f"Cannot move an asset from '{UnitStatus(from_status).label}' to "
            f"'{UnitStatus(to_status).label}'."
        )


def validate_transferable(status):
    if status not in TRANSFERABLE_STATUSES:
        raise ValidationError(f"Cannot transfer an asset with status '{UnitStatus(status).label}'.")
