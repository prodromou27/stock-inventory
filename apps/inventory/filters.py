"""Shared filter application for UnitAsset/StockBalance querysets — spec §14's
filter list, applied on top of whatever apps.locations.scoping.scope_queryset
already restricted the queryset to. Kept out of views.py so it's reusable
across the inventory list screens and the reporting app.
"""

from uuid import UUID

from django.db.models import Count, Exists, OuterRef, Q

from apps.core.dates import parse_date_param
from apps.locations.models import Location


def _get(params, key):
    return (params.get(key) or "").strip()


def duplicate_serial_values(queryset):
    """Normalized serials that appear more than once in `queryset`."""
    return (
        queryset.exclude(normalized_serial="")
        .values("normalized_serial")
        .annotate(occurrences=Count("id"))
        .filter(occurrences__gt=1)
        .values_list("normalized_serial", flat=True)
    )


def _filter_by_location(queryset, params, *, location_field):
    # Match named ancestors in SQL; the caller has already scoped the stock.
    # A shelf belongs to its room/country even when those names aren't on
    # the shelf row itself. EXISTS avoids duplicates and loading location IDs.
    for key, level in (
        ("country", Location.Level.COUNTRY),
        ("storage_room", Location.Level.STORAGE_ROOM),
        ("shelf", Location.Level.RACK_SHELF),
    ):
        if name := _get(params, key):
            match = {"name__icontains": name}
            if name.startswith("id:"):
                try:
                    match = {"pk": UUID(name[3:])}
                except ValueError:
                    return queryset.none()
            queryset = queryset.filter(
                Exists(
                    Location.objects.filter(
                        level=level,
                        **match,
                        path__ancestor_or_self=OuterRef(f"{location_field}__path"),
                    )
                )
            )
    location_id = _get(params, "location")
    if not location_id:
        return queryset
    try:
        location_id = UUID(location_id)
    except ValueError:
        return queryset.none()
    location = Location.objects.filter(pk=location_id).first()
    if location is None:
        return queryset.none()
    return queryset.filter(**{f"{location_field}__path__descendant_or_self": location.path})


def filter_unit_assets(queryset, params):
    if name := _get(params, "name"):
        queryset = queryset.filter(name__icontains=name)
    q = _get(params, "q")
    if q:
        queryset = queryset.filter(
            Q(name__icontains=q)
            | Q(normalized_serial__icontains=q.upper())
            | Q(product__brand__name__icontains=q)
            | Q(product__model__icontains=q)
            | Q(product__sku__icontains=q)
            | Q(product__product_type__name__icontains=q)
            | Q(project_reference__icontains=q)
            | Q(final_customer__icontains=q)
        )

    if brand := _get(params, "brand"):
        queryset = queryset.filter(product__brand__name__icontains=brand)
    if model := _get(params, "model"):
        queryset = queryset.filter(product__model__icontains=model)
    if sku := _get(params, "sku"):
        queryset = queryset.filter(product__sku__icontains=sku)
    if product_type := (_get(params, "type") or _get(params, "product_type")):
        queryset = queryset.filter(product__product_type__name__icontains=product_type)
    if serial := _get(params, "serial"):
        queryset = queryset.filter(normalized_serial__icontains=serial.upper())
    if status := _get(params, "status"):
        queryset = queryset.filter(status=status)
    if _get(params, "in_storage") == "1":
        queryset = queryset.filter(current_location__isnull=False)
    if condition := _get(params, "condition"):
        queryset = queryset.filter(condition=condition)
    if stock_purpose := _get(params, "stock_purpose"):
        queryset = queryset.filter(stock_purpose=stock_purpose)
    if project_reference := _get(params, "project_reference"):
        queryset = queryset.filter(project_reference__icontains=project_reference)
    if final_customer := _get(params, "final_customer"):
        queryset = queryset.filter(final_customer__icontains=final_customer)
    if supplier := _get(params, "supplier"):
        queryset = queryset.filter(supplier__icontains=supplier)
    if invoice_number := _get(params, "invoice_number"):
        queryset = queryset.filter(invoice_number__icontains=invoice_number)

    if arrival_after := parse_date_param(_get(params, "arrival_after")):
        queryset = queryset.filter(arrival_date__gte=arrival_after)
    if arrival_before := parse_date_param(_get(params, "arrival_before")):
        queryset = queryset.filter(arrival_date__lte=arrival_before)
    if removal_after := parse_date_param(_get(params, "removal_after")):
        queryset = queryset.filter(last_removal_date__gte=removal_after)
    if removal_before := parse_date_param(_get(params, "removal_before")):
        queryset = queryset.filter(last_removal_date__lte=removal_before)

    queryset = _filter_by_location(queryset, params, location_field="current_location")

    if _get(params, "duplicate_serial") == "1":
        queryset = queryset.filter(normalized_serial__in=list(duplicate_serial_values(queryset)))

    return queryset


def filter_stock_balances(queryset, params):
    if _get(params, "in_storage") == "1":
        queryset = queryset.filter(on_hand_quantity__gt=0)
    q = _get(params, "q")
    if q:
        queryset = queryset.filter(
            Q(product__brand__name__icontains=q)
            | Q(product__model__icontains=q)
            | Q(product__sku__icontains=q)
            | Q(product__product_type__name__icontains=q)
        )

    if brand := _get(params, "brand"):
        queryset = queryset.filter(product__brand__name__icontains=brand)
    if model := _get(params, "model"):
        queryset = queryset.filter(product__model__icontains=model)
    if sku := _get(params, "sku"):
        queryset = queryset.filter(product__sku__icontains=sku)
    if product_type := (_get(params, "type") or _get(params, "product_type")):
        queryset = queryset.filter(product__product_type__name__icontains=product_type)
    if stock_purpose := _get(params, "stock_purpose"):
        queryset = queryset.filter(stock_purpose=stock_purpose)

    return _filter_by_location(queryset, params, location_field="location")
