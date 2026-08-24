from apps.locations.scoping import scope_queryset

from ..models import UnitAsset


def normalize_serial(value):
    return " ".join((value or "").split()).upper()


def check_duplicate_serial(vendor_serial, *, user, exclude_id=None):
    """Matches scoped to what `user` can see — an out-of-scope match must
    never leak through this check (docs/architecture/05-tracking-and-duplicates.md).
    """
    normalized = normalize_serial(vendor_serial)
    if not normalized:
        return UnitAsset.objects.none()

    queryset = scope_queryset(
        user,
        UnitAsset.objects.select_related("product", "product__brand", "current_location"),
        location_field="current_location",
    )
    queryset = queryset.filter(normalized_serial=normalized)
    if exclude_id:
        queryset = queryset.exclude(pk=exclude_id)
    return queryset
