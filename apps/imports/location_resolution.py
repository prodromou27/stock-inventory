"""Resolves the legacy LOCATION + '2nd floor Location' columns to a real
Location row by name match — never silently creates a Location (doc 07:
"unknown locations reported, not silently created"). Ambiguous or unmatched
values are left unresolved for the preview screen's per-row override.
"""

from django.db.models import Q

from apps.locations.models import Location


def resolve_location(location_text, sub_location_text):
    """Returns (Location_or_None, detail_message_or_empty)."""
    if not location_text:
        return None, "No location given."

    candidates = list(Location.objects.filter(name__iexact=location_text, is_active=True))
    if not candidates:
        return None, f"Unknown location '{location_text}' — no matching active location found."

    if not sub_location_text:
        if len(candidates) == 1:
            return candidates[0], ""
        return None, (
            f"Location name '{location_text}' matches {len(candidates)} different locations — "
            "remap this row to a specific one."
        )

    sub_text = str(sub_location_text).strip()
    narrowed = list(
        Location.objects.filter(parent__in=candidates, is_active=True).filter(
            Q(name__iexact=sub_text) | Q(code__iexact=sub_text)
        )
    )
    if len(narrowed) == 1:
        return narrowed[0], ""
    if len(narrowed) > 1:
        return None, (
            f"'{location_text}' / '{sub_text}' matches {len(narrowed)} locations — "
            "remap this row to a specific one."
        )

    # No child matched the sub-location value — fall back to the parent
    # match itself, but only when it was unambiguous.
    if len(candidates) == 1:
        return candidates[0], (
            f"Sub-location '{sub_text}' under '{location_text}' was not found; "
            "resolved to the parent location instead."
        )
    return None, (
        f"Location name '{location_text}' matches {len(candidates)} different locations — "
        "remap this row to a specific one."
    )
