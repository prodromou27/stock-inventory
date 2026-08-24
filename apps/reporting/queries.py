"""Query builders for spec §15's reports. Every function scopes its
queryset through apps.locations.scoping/apps.inventory.access before
returning — there is no report-specific authorization path, matching
docs/architecture/04-permission-matrix.md ("reports honor user storage
permissions" the same way list screens do).
"""

from django.db.models import Count, F, Sum

from apps.inventory.access import scope_asset_status_history_queryset, scope_transaction_queryset
from apps.inventory.models import (
    AssetStatusHistory,
    InventoryTransaction,
    MovementType,
    StockBalance,
    StockReservation,
    UnitAsset,
    UnitStatus,
)
from apps.locations.scoping import scope_queryset

_ASSET_RELATED = ("product", "product__brand", "product__product_type", "current_location")
_BALANCE_RELATED = ("product", "product__brand", "product__product_type", "location")


def _scoped_assets(user, **status_filter):
    queryset = scope_queryset(
        user, UnitAsset.objects.select_related(*_ASSET_RELATED), location_field="current_location"
    )
    return queryset.filter(**status_filter)


def _scoped_balances(user):
    return scope_queryset(
        user, StockBalance.objects.select_related(*_BALANCE_RELATED), location_field="location"
    )


def current_stock(user):
    units = _scoped_assets(user, status=UnitStatus.IN_STOCK).order_by(
        "product__brand__name", "product__model"
    )
    balances = (
        _scoped_balances(user)
        .filter(on_hand_quantity__gt=0)
        .order_by("product__brand__name", "product__model")
    )
    return units, balances


def stock_by_location(user):
    """One row per Location that has any in-stock units or on-hand quantity
    in scope: unit count and total quantity.
    """
    unit_counts = dict(
        _scoped_assets(user, status=UnitStatus.IN_STOCK)
        .values("current_location")
        .annotate(count=Count("id"))
        .values_list("current_location", "count")
    )
    balance_totals = dict(
        _scoped_balances(user)
        .values("location")
        .annotate(total=Sum("on_hand_quantity"))
        .values_list("location", "total")
    )

    from apps.locations.models import Location

    location_ids = set(unit_counts) | set(balance_totals)
    location_ids.discard(None)
    locations = Location.objects.filter(pk__in=location_ids)

    rows = [
        {
            "location": location,
            "unit_count": unit_counts.get(location.pk, 0),
            "quantity_total": balance_totals.get(location.pk, 0),
        }
        for location in locations
    ]
    rows.sort(key=lambda row: (row["location"].level, row["location"].name))
    return rows


def reserved_stock(user):
    units = _scoped_assets(user, status=UnitStatus.RESERVED).order_by(
        "product__brand__name", "product__model"
    )
    reservations = scope_queryset(
        user,
        StockReservation.objects.select_related("product", "product__brand", "location").filter(
            status="active"
        ),
        location_field="location",
    ).order_by("-created_at")
    return units, reservations


def employee_assignments(user):
    return scope_transaction_queryset(
        user,
        InventoryTransaction.objects.filter(movement_type=MovementType.ASSIGNMENT).select_related(
            "performed_by"
        ),
    ).order_by("-occurred_at")


def customer_deliveries(user):
    return scope_transaction_queryset(
        user,
        InventoryTransaction.objects.filter(movement_type=MovementType.DELIVERY).select_related(
            "performed_by"
        ),
    ).order_by("-occurred_at")


def stock_by_project_reference(user, project_reference=""):
    if project_reference:
        units = (
            _scoped_assets(user)
            .exclude(project_reference="")
            .filter(project_reference__icontains=project_reference)
        )
        reservations = scope_queryset(
            user,
            StockReservation.objects.select_related("product", "product__brand", "location"),
            location_field="location",
        ).filter(project_reference__icontains=project_reference)
        return units, reservations

    distinct_refs = (
        _scoped_assets(user)
        .exclude(project_reference="")
        .values_list("project_reference", flat=True)
        .distinct()
        .order_by("project_reference")
    )
    return distinct_refs, None


def temporary_assignments(user):
    """No overdue automation (spec §9/§16) — expected_return_date is shown
    as informational only, never computed against "today" or highlighted.
    """
    return scope_transaction_queryset(
        user,
        InventoryTransaction.objects.filter(
            movement_type=MovementType.ASSIGNMENT, is_temporary_assignment=True
        ).select_related("performed_by"),
    ).order_by("-occurred_at")


def damaged_assets(user):
    return _scoped_assets(user, status=UnitStatus.DAMAGED).order_by(
        "product__brand__name", "product__model"
    )


def lost_assets(user):
    return _scoped_assets(user, status=UnitStatus.LOST).order_by(
        "product__brand__name", "product__model"
    )


def disposed_items(user):
    """Particular use for HDD disposal records (spec §9/§15) — disposed
    assets are never deleted, so this stays queryable indefinitely; the
    Type column (from product_type) is what identifies HDDs.
    """
    return _scoped_assets(user, status=UnitStatus.DISPOSED).order_by(
        "product__product_type__name", "product__brand__name", "product__model"
    )


def movement_history(user, unit_asset=None):
    queryset = scope_asset_status_history_queryset(
        user,
        AssetStatusHistory.objects.select_related(
            "unit_asset",
            "unit_asset__product",
            "transaction",
            "from_location",
            "to_location",
            "recorded_by",
        ),
    )
    if unit_asset is not None:
        queryset = queryset.filter(unit_asset=unit_asset)
    return queryset.order_by("-occurred_at")


def low_stock_balances(user):
    """Disabled unless configured (spec §16) — only products with a
    low_stock_threshold set are considered at all.
    """
    return (
        _scoped_balances(user)
        .filter(product__low_stock_threshold__isnull=False)
        .annotate(available=F("on_hand_quantity") - F("reserved_quantity"))
        .filter(available__lte=F("product__low_stock_threshold"))
        .order_by("product__brand__name", "product__model")
    )
