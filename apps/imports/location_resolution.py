"""Resolves the legacy LOCATION + '2nd floor Location' columns to a real
Location row by name match — never silently creates a Location (doc 07:
"unknown locations reported, not silently created"). Ambiguous or unmatched
values are left unresolved for the preview screen's per-row override.
"""

from django.db.models import Q

from apps.locations.models import LEVELS_ABOVE_ROOM, Location


def _too_high(location):
    return location.level in LEVELS_ABOVE_ROOM


def resolve_location(location_text, sub_location_text):
    """Returns (Location_or_None, detail_message_or_empty).

    A Country is never a valid final stock location (spec: "the
    Storage Room is required") — a resolution that would land there,
    including a would-be parent fallback, is reported as unresolved instead
    of silently accepted, matching the same rule enforced everywhere else
    stock gets a location.
    """
    if not location_text:
        return None, "No location given."

    candidates = list(Location.objects.filter(name__iexact=location_text, is_active=True))
    if not candidates:
        return None, f"Unknown location '{location_text}' — no matching active location found."

    if not sub_location_text:
        if len(candidates) == 1:
            match = candidates[0]
            if _too_high(match):
                return None, (
                    f"'{location_text}' is a {match.get_level_display()}, not a storage "
                    "location — remap this row to a specific Storage Room (or Rack/Shelf)."
                )
            return match, ""
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
        match = narrowed[0]
        if _too_high(match):
            return None, (
                f"'{location_text}' / '{sub_text}' is a {match.get_level_display()}, not a "
                "storage location — remap this row to a specific Storage Room (or Rack/Shelf)."
            )
        return match, ""
    if len(narrowed) > 1:
        return None, (
            f"'{location_text}' / '{sub_text}' matches {len(narrowed)} locations — "
            "remap this row to a specific one."
        )

    # No child matched the sub-location value. Falling back to the parent
    # match itself is only ever offered when that parent is unambiguous AND
    # itself a valid stock location (Storage Room or below) — never a
    # Country, which "do not guess locations" rules out outright.
    if len(candidates) == 1:
        parent = candidates[0]
        if _too_high(parent):
            return None, (
                f"Sub-location '{sub_text}' under '{location_text}' was not found, and "
                f"'{location_text}' itself is a {parent.get_level_display()}, not a storage "
                "location — remap this row to a specific Storage Room (or Rack/Shelf)."
            )
        return parent, (
            f"Sub-location '{sub_text}' under '{location_text}' was not found; "
            "resolved to the parent location instead."
        )
    return None, (
        f"Location name '{location_text}' matches {len(candidates)} different locations — "
        "remap this row to a specific one."
    )
