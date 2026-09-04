"""Query builders for spec §15's reports. Every function scopes its
queryset through apps.locations.scoping/apps.inventory.access before
returning — there is no report-specific authorization path, matching
docs/architecture/04-permission-matrix.md ("reports honor user storage
permissions" the same way list screens do).
"""

from datetime import timedelta

from django.db.models import Count, F, Sum
from django.utils import timezone

from apps.inventory.access import scope_asset_status_history_queryset, scope_transaction_queryset
from apps.inventory.filters import duplicate_serial_values
from apps.inventory.models import (
    AssetStatusHistory,
    InventoryTransaction,
    MovementType,
    ReservationStatus,
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


def recent_transactions(user, limit=8):
    """apps.core.views.HomeView's "Recent activity" list — the most recent
    transactions the user can see, across every movement type, scoped the
    same way the full Transactions list already is.
    """
    return scope_transaction_queryset(
        user, InventoryTransaction.objects.select_related("performed_by")
    ).order_by("-occurred_at", "-created_at")[:limit]


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
            .order_by("product__brand__name", "product__model")
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


def dashboard_summary(user):
    """apps.core.views.HomeView's stat cards — every number scoped to the
    user's accessible locations the same way every list/report screen is
    (this module's docstring). Cheap: every value is a .count()/aggregate,
    never a loaded queryset, so this is safe to run on every dashboard
    visit regardless of inventory size (spec §21.15's pagination/volume
    concern applies here too, even though there's no list to paginate).
    """
    # occurred_at is a DateField, not DateTimeField.
    since = timezone.now().date() - timedelta(days=7)
    on_hand_total = _scoped_balances(user).aggregate(total=Sum("on_hand_quantity"))["total"] or 0
    return {
        "assets_in_stock": _scoped_assets(user, status=UnitStatus.IN_STOCK).count(),
        "quantity_on_hand": on_hand_total,
        "low_stock_count": low_stock_balances(user).count(),
        "active_reservations": scope_queryset(
            user,
            StockReservation.objects.filter(status=ReservationStatus.ACTIVE),
            location_field="location",
        ).count(),
        "damaged_count": damaged_assets(user).count(),
        "lost_count": lost_assets(user).count(),
        "recent_transactions": scope_transaction_queryset(
            user, InventoryTransaction.objects.filter(occurred_at__gte=since)
        ).count(),
    }


# Only these statuses are ever supposed to leave current_location NULL
# (docs/architecture/03-status-and-movement-rules.md's transition table:
# assignment/delivery/loss/disposal all set current_location -> NULL as
# part of the asset leaving storage). Anything else with a NULL location is
# a genuine data-integrity gap, not a normal state.
_STATUSES_WITHOUT_LOCATION = (
    UnitStatus.ASSIGNED,
    UnitStatus.DELIVERED,
    UnitStatus.LOST,
    UnitStatus.DISPOSED,
)


def data_quality_summary(user):
    """The Dashboard's "Data quality" panel — issues surfaced from data
    that's already queryable elsewhere, never a new detection rule:
    duplicate serials (duplicate_serial_values(), the same set the Assets
    grid's "Duplicate serials only" filter already uses) and assets missing
    a current_location despite a status that should always carry one.
    """
    assets = _scoped_assets(user)
    return {
        "duplicate_serial_count": duplicate_serial_values(assets).count(),
        "unlocated_count": assets.filter(current_location__isnull=True)
        .exclude(status__in=_STATUSES_WITHOUT_LOCATION)
        .count(),
    }
