"""Query builders for spec §15's reports. Every function scopes its
queryset through apps.locations.scoping/apps.inventory.access before
returning — there is no report-specific authorization path, matching
docs/architecture/04-permission-matrix.md ("reports honor user storage
permissions" the same way list screens do).
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count, F, OuterRef, Q, Subquery, Sum, UUIDField
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Product, TrackingMethod
from apps.inventory.access import (
    scope_asset_queryset,
    scope_asset_status_history_queryset,
    scope_transaction_queryset,
)
from apps.inventory.filters import duplicate_serial_values
from apps.inventory.models import (
    AssetStatusHistory,
    InventoryTransaction,
    InventoryTransactionLine,
    MovementType,
    ProductLocationThreshold,
    ReservationStatus,
    StockBalance,
    StockPurpose,
    StockReservation,
    UnitAsset,
    UnitStatus,
)
from apps.locations.models import Location
from apps.locations.scoping import accessible_locations, country_for_location, scope_queryset

_ASSET_RELATED = ("product", "product__brand", "product__product_type", "current_location")
_BALANCE_RELATED = ("product", "product__brand", "product__product_type", "location")
_REUSABLE_UNIT_STATUSES = (UnitStatus.IN_STOCK, UnitStatus.RETURNED)


@dataclass(frozen=True)
class LowStockItem:
    """One normalized alert row for either inventory tracking method."""

    product: object
    location: object
    available_quantity: int
    threshold: int
    tracking_method: str
    balance: object = None

    @property
    def available(self):
        return self.available_quantity

    @property
    def detail_url(self):
        if self.balance is not None:
            return self.balance.get_absolute_url()
        return (
            f"{reverse('inventory:asset_list')}?product={self.product.pk}"
            f"&location={self.location.pk}&in_storage=1"
        )

    def get_absolute_url(self):
        """Compatibility with balance-like report rows and existing callers."""
        return self.detail_url

    @property
    def stock_kind(self):
        if self.balance is not None:
            return self.balance.get_stock_purpose_display()
        return "Individual assets"


def _scoped_assets(user, **status_filter):
    queryset = scope_asset_queryset(user, UnitAsset.objects.select_related(*_ASSET_RELATED))
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
    # Location.LEVEL_ORDER.index(...), not the raw level string — see
    # apps.locations.models.order_by_hierarchy()'s docstring for why sorting
    # by the stored string scrambles the actual hierarchy order.
    rows.sort(
        key=lambda row: (Location.LEVEL_ORDER.index(row["location"].level), row["location"].name)
    )
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


def _quantity_balances_with_threshold(user, location=None):
    threshold_override = scope_queryset(
        user, ProductLocationThreshold.objects.all(), location_field="location"
    ).filter(product_id=OuterRef("product_id"), location_id=OuterRef("location_id"))
    threshold_override = threshold_override.values("low_stock_threshold")[:1]
    queryset = (
        _scoped_balances(user)
        .annotate(
            configured_threshold=Coalesce(
                Subquery(threshold_override), F("product__low_stock_threshold")
            ),
            available=StockBalance.AVAILABLE_QUANTITY_EXPRESSION,
        )
        .filter(configured_threshold__isnull=False, product__is_active=True)
    )
    if location is not None:
        queryset = queryset.filter(location__path__descendant_or_self=location.path)
    return queryset


def low_stock_balances(user, location=None):
    """Disabled unless configured (spec §16) — only balances with a product
    default or exact-location threshold are considered. `location` (any level,
    including a Country) optionally restricts to that location and its
    descendants — apps.reporting.views.LowStockView's country/location
    filter, same ltree descendant-or-self match every other location filter
    in this app uses (apps.inventory.filters._filter_by_location).
    """
    return (
        _quantity_balances_with_threshold(user, location=location)
        .filter(available__lte=F("configured_threshold"))
        .order_by("product__brand__name", "product__model")
    )


def _unit_stock_items(user, location=None, *, only_low):
    """Configured unit-stock rows, grouped by Country rather than room.

    All scoped assets establish the product/Country association, including
    issued assets whose last storage location is used after current_location
    becomes NULL. That keeps a zero-available alert alive after the last unit
    is issued. An explicit Country override also establishes an association
    before the first receipt.
    """
    scoped_locations = list(accessible_locations(user).select_related("parent", "parent__parent"))
    country_by_location_id = {item.pk: country_for_location(item) for item in scoped_locations}
    countries = {
        country.pk: country for country in country_by_location_id.values() if country is not None
    }
    selected_country = country_by_location_id.get(location.pk) if location is not None else None
    if location is not None and selected_country is None:
        return []

    overrides = list(
        scope_queryset(user, ProductLocationThreshold.objects.all(), location_field="location")
        .filter(
            product__tracking_method=TrackingMethod.UNIT,
            product__is_active=True,
            location_id__in=countries,
            location__level=Location.Level.COUNTRY,
        )
        .select_related("product__brand", "product__product_type", "location")
    )
    override_by_pair = {(item.product_id, item.location_id): item for item in overrides}
    configured_product_ids = set(
        Product.objects.filter(
            tracking_method=TrackingMethod.UNIT,
            is_active=True,
            low_stock_threshold__isnull=False,
        ).values_list("pk", flat=True)
    )
    configured_product_ids.update(
        item.product_id for item in overrides if item.low_stock_threshold is not None
    )
    if not configured_product_ids:
        return []

    last_from_location = (
        InventoryTransactionLine.objects.filter(
            unit_asset_id=OuterRef("pk"), from_location__isnull=False
        )
        .order_by("-transaction__created_at", "-line_number")
        .values("from_location_id")[:1]
    )
    aggregates = (
        _scoped_assets(user)
        .filter(product_id__in=configured_product_ids, product__is_active=True)
        .annotate(
            alert_location_id=Coalesce(
                "current_location_id", Subquery(last_from_location), output_field=UUIDField()
            )
        )
        .values("product_id", "alert_location_id")
        .annotate(available_quantity=Count("id", filter=Q(status__in=_REUSABLE_UNIT_STATUSES)))
    )

    available_by_pair = {}
    associated_pairs = set()
    for aggregate in aggregates:
        country = country_by_location_id.get(aggregate["alert_location_id"])
        if country is None or (selected_country and country.pk != selected_country.pk):
            continue
        pair = (aggregate["product_id"], country.pk)
        associated_pairs.add(pair)
        available_by_pair[pair] = available_by_pair.get(pair, 0) + aggregate["available_quantity"]

    for override in overrides:
        if override.low_stock_threshold is None:
            continue
        if selected_country and override.location_id != selected_country.pk:
            continue
        associated_pairs.add((override.product_id, override.location_id))

    products = {
        product.pk: product
        for product in Product.objects.filter(
            pk__in={pair[0] for pair in associated_pairs}, is_active=True
        )
        .select_related("brand", "product_type")
        .order_by("brand__name", "model")
    }
    rows = []
    for product_id, country_id in associated_pairs:
        product = products.get(product_id)
        country = countries.get(country_id)
        if product is None or country is None:
            continue
        override = override_by_pair.get((product_id, country_id))
        threshold = (
            override.low_stock_threshold
            if override is not None and override.low_stock_threshold is not None
            else product.low_stock_threshold
        )
        if threshold is None:
            continue
        available = available_by_pair.get((product_id, country_id), 0)
        if only_low and available > threshold:
            continue
        rows.append(
            LowStockItem(
                product=product,
                location=country,
                available_quantity=available,
                threshold=threshold,
                tracking_method=TrackingMethod.UNIT,
            )
        )
    return sorted(
        rows, key=lambda row: (row.product.brand.name, row.product.model, row.location.name)
    )


def low_stock_items(user, location=None):
    quantity_rows = [
        LowStockItem(
            product=balance.product,
            location=balance.location,
            available_quantity=balance.available_quantity,
            threshold=balance.configured_threshold,
            tracking_method=TrackingMethod.QUANTITY,
            balance=balance,
        )
        for balance in low_stock_balances(user, location=location)
    ]
    return sorted(
        quantity_rows + _unit_stock_items(user, location=location, only_low=True),
        key=lambda row: (row.product.brand.name, row.product.model, row.location.name),
    )


def has_configured_low_stock(user, location=None):
    return _quantity_balances_with_threshold(user, location=location).exists() or bool(
        _unit_stock_items(user, location=location, only_low=False)
    )


def reorder_suggestions(user, location=None):
    """Same base set as low_stock_balances() (below its configured
    threshold) — this report answers "how much," not "whether," so it only
    makes sense for items already flagged as low. Never invents a number:
    a product with no target_stock_level configured (product-wide, or
    overridden for this specific location via ProductLocationThreshold)
    shows "Configuration required" rather than a guess. Every row is
    labeled a *suggestion* — this is not a purchase order, supplier
    ordering, or invoicing feature, and creates nothing on its own.

    Receipt metadata is annotated with correlated subqueries so query count
    remains bounded when many products are below threshold.
    """
    last_receipt = InventoryTransactionLine.objects.filter(
        product_id=OuterRef("product_id"),
        to_location_id=OuterRef("location_id"),
        transaction__movement_type=MovementType.RECEIPT,
    ).order_by("-transaction__occurred_at", "-transaction__created_at")
    balances = list(
        low_stock_balances(user, location=location).annotate(
            last_receipt_date=Subquery(last_receipt.values("transaction__occurred_at")[:1]),
            last_receipt_quantity=Subquery(last_receipt.values("quantity_delta")[:1]),
            last_invoice_number=Subquery(last_receipt.values("invoice_number_snapshot")[:1]),
        )
    )
    overrides = {
        (t.product_id, t.location_id): t
        for t in scope_queryset(
            user, ProductLocationThreshold.objects.all(), location_field="location"
        ).filter(
            product_id__in={b.product_id for b in balances},
            location_id__in={b.location_id for b in balances},
        )
    }

    rows = []
    for balance in balances:
        override = overrides.get((balance.product_id, balance.location_id))
        target = balance.product.target_stock_level
        min_reorder = balance.product.min_reorder_quantity
        supplier = balance.product.preferred_supplier
        if override is not None:
            if override.target_stock_level is not None:
                target = override.target_stock_level
            if override.min_reorder_quantity is not None:
                min_reorder = override.min_reorder_quantity
            if override.preferred_supplier:
                supplier = override.preferred_supplier

        available = balance.available_quantity
        suggested_quantity = None if target is None else max(target - available, min_reorder or 0)

        rows.append(
            {
                "balance": balance,
                "product": balance.product,
                "location": balance.location,
                "available_quantity": available,
                "low_stock_threshold": balance.configured_threshold,
                "target_stock_level": target,
                "min_reorder_quantity": min_reorder,
                "preferred_supplier": supplier,
                "suggested_quantity": suggested_quantity,
                "configuration_required": target is None,
                "last_receipt_date": balance.last_receipt_date,
                "last_receipt_quantity": balance.last_receipt_quantity,
                "last_invoice_number": balance.last_invoice_number or "",
                "detail_url": balance.get_absolute_url(),
            }
        )

    unit_items = _unit_stock_items(user, location=location, only_low=True)
    unit_overrides = {
        (override.product_id, override.location_id): override
        for override in scope_queryset(
            user, ProductLocationThreshold.objects.all(), location_field="location"
        ).filter(
            product_id__in={item.product.pk for item in unit_items},
            location_id__in={item.location.pk for item in unit_items},
        )
    }
    for item in unit_items:
        override = unit_overrides.get((item.product.pk, item.location.pk))
        target = item.product.target_stock_level
        min_reorder = item.product.min_reorder_quantity
        supplier = item.product.preferred_supplier
        if override is not None:
            if override.target_stock_level is not None:
                target = override.target_stock_level
            if override.min_reorder_quantity is not None:
                min_reorder = override.min_reorder_quantity
            if override.preferred_supplier:
                supplier = override.preferred_supplier
        suggested_quantity = (
            None if target is None else max(target - item.available_quantity, min_reorder or 0)
        )
        rows.append(
            {
                "balance": None,
                "product": item.product,
                "location": item.location,
                "available_quantity": item.available_quantity,
                "low_stock_threshold": item.threshold,
                "target_stock_level": target,
                "min_reorder_quantity": min_reorder,
                "preferred_supplier": supplier,
                "suggested_quantity": suggested_quantity,
                "configuration_required": target is None,
                "last_receipt_date": None,
                "last_receipt_quantity": None,
                "last_invoice_number": "",
                "detail_url": item.detail_url,
            }
        )
    rows.sort(
        key=lambda row: (
            row["product"].brand.name,
            row["product"].model,
            row["location"].name,
        )
    )
    return rows


def dashboard_summary(user):
    """apps.core.views.HomeView's stat cards — every number scoped to the
    user's accessible locations the same way every list/report screen is
    (this module's docstring). Cheap: every value is a .count()/aggregate,
    never a loaded queryset, so this is safe to run on every dashboard
    visit regardless of inventory size (spec §21.15's pagination/volume
    concern applies here too, even though there's no list to paginate).
    """
    # occurred_at is a DateField, not DateTimeField.
    since = timezone.localdate() - timedelta(days=7)
    on_hand_total = _scoped_balances(user).aggregate(total=Sum("on_hand_quantity"))["total"] or 0
    scoped_assets = _scoped_assets(user)
    return {
        "assets_in_stock": _scoped_assets(user, status=UnitStatus.IN_STOCK).count(),
        "quantity_on_hand": on_hand_total,
        "low_stock_count": len(low_stock_items(user)),
        "internal_stock_count": scoped_assets.filter(stock_purpose=StockPurpose.INTERNAL).count(),
        "customer_stock_count": scoped_assets.filter(stock_purpose=StockPurpose.CUSTOMER).count(),
        "active_reservations": scope_queryset(
            user,
            StockReservation.objects.filter(status=ReservationStatus.ACTIVE),
            location_field="location",
        ).count(),
        "assigned_count": _scoped_assets(user, status=UnitStatus.ASSIGNED).count(),
        "delivered_count": _scoped_assets(user, status=UnitStatus.DELIVERED).count(),
        "damaged_count": damaged_assets(user).count(),
        "lost_count": lost_assets(user).count(),
        "disposed_count": _scoped_assets(user, status=UnitStatus.DISPOSED).count(),
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
    UnitStatus.IN_USE,
    UnitStatus.ASSIGNED,
    UnitStatus.DELIVERED,
    UnitStatus.LOST,
    UnitStatus.DISPOSED,
)

# The only two statuses write_unit_line() (apps.inventory.services.ledger)
# ever attaches a current_custody_transaction pointer for — an asset in one
# of these without one is a genuine data-integrity gap (pre-dates this
# feature, e.g. rows created before the field existed), not a normal state.
_CUSTODY_STATUSES = (UnitStatus.ASSIGNED, UnitStatus.DELIVERED)


def data_quality_summary(user):
    """The Dashboard's "Data quality" panel — issues surfaced from data
    that's already queryable elsewhere, never a new detection rule:
    duplicate serials (duplicate_serial_values(), the same set the Assets
    grid's "Duplicate serials only" filter already uses), assets missing a
    current_location despite a status that should always carry one, and
    assets missing their current-custodian pointer despite being Assigned/
    Delivered.
    """
    assets = _scoped_assets(user)
    return {
        "duplicate_serial_count": duplicate_serial_values(assets).count(),
        "unlocated_count": assets.filter(current_location__isnull=True)
        .exclude(status__in=_STATUSES_WITHOUT_LOCATION)
        .count(),
        "missing_custodian_count": assets.filter(
            status__in=_CUSTODY_STATUSES, current_custody_transaction__isnull=True
        ).count(),
    }
