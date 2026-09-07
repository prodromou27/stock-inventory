"""The 12 detection checks apps.dataquality.services.run_detection() runs.
Every function takes `breadcrumbs` (apps.locations.scoping.
location_breadcrumb_map(), computed once per run by the caller rather than
once per check) and is a generator yielding one dict per finding:
{issue_type, severity, object_type, object_id, country, location_label,
explanation, recommended_correction} — services.py turns each into a
DataQualityFinding upsert. None of these ever mutate anything; detection is
strictly read-only, matching the "must NOT create notifications or
auto-change stock/history" requirement.

3 of the 13 port directly from apps.reporting.queries.data_quality_summary()
(the Dashboard's existing "Data quality" panel, left completely unmodified
— dashboard changes are out of scope for this feature): duplicate serials,
missing location, missing custodian. The other 10 are new.
"""

from django.db.models import OuterRef, Subquery

from apps.catalog.models import ItemCategory, Product
from apps.inventory.filters import duplicate_serial_values
from apps.inventory.models import (
    AssetStatusHistory,
    Condition,
    StockBalance,
    StockPurpose,
    UnitAsset,
    UnitStatus,
)
from apps.locations.models import ROOM_OR_BELOW_LEVELS, Location

from .models import DataQualityIssueType, DataQualitySeverity

# Same allow-list apps.reporting.queries.data_quality_summary() uses for its
# "missing location"/"missing custodian" checks — duplicated rather than
# imported so apps.dataquality doesn't reach into apps.reporting for two
# constants (the two checks share the underlying business rule, not code
# that needs a single source of truth to stay correct).
_STATUSES_WITHOUT_LOCATION = (
    UnitStatus.ASSIGNED,
    UnitStatus.DELIVERED,
    UnitStatus.LOST,
    UnitStatus.DISPOSED,
)
_CUSTODY_STATUSES = (UnitStatus.ASSIGNED, UnitStatus.DELIVERED)
_ON_BOOKS_STATUSES = (UnitStatus.IN_STOCK, UnitStatus.RESERVED)


def _location_context(breadcrumbs, location):
    if location is None:
        return "", ""
    return breadcrumbs.get(location.pk, {}).get("country", ""), str(location)


def check_duplicate_serial(breadcrumbs):
    assets = UnitAsset.objects.select_related("product", "product__brand", "current_location")
    duplicated = set(duplicate_serial_values(assets))
    if not duplicated:
        return
    for asset in assets.filter(normalized_serial__in=duplicated):
        country, location_label = _location_context(breadcrumbs, asset.current_location)
        yield {
            "issue_type": DataQualityIssueType.DUPLICATE_SERIAL,
            "severity": DataQualitySeverity.HIGH,
            "object_type": "UnitAsset",
            "object_id": str(asset.pk),
            "country": country,
            "location_label": location_label,
            "explanation": f'Serial "{asset.vendor_serial}" is shared with at least one other '
            "asset.",
            "recommended_correction": "Confirm which unit actually carries this serial and correct "
            "or clear the serial on the others.",
        }


def check_missing_location(breadcrumbs):
    assets = (
        UnitAsset.objects.select_related("product", "product__brand")
        .filter(current_location__isnull=True)
        .exclude(status__in=_STATUSES_WITHOUT_LOCATION)
    )
    for asset in assets:
        yield {
            "issue_type": DataQualityIssueType.MISSING_LOCATION,
            "severity": DataQualitySeverity.HIGH,
            "object_type": "UnitAsset",
            "object_id": str(asset.pk),
            "country": "",
            "location_label": "",
            "explanation": f'Status is "{asset.get_status_display()}" but no location is recorded.',
            "recommended_correction": "Use an Administrator correction to set the correct current "
            "location, or the correct status if the asset genuinely has none.",
        }


def check_missing_custodian(breadcrumbs):
    assets = UnitAsset.objects.select_related("product", "product__brand").filter(
        status__in=_CUSTODY_STATUSES, current_custody_transaction__isnull=True
    )
    for asset in assets:
        yield {
            "issue_type": DataQualityIssueType.MISSING_CUSTODIAN,
            "severity": DataQualitySeverity.MEDIUM,
            "object_type": "UnitAsset",
            "object_id": str(asset.pk),
            "country": "",
            "location_label": "",
            "explanation": f'Status is "{asset.get_status_display()}" but no assignment/delivery '
            'transaction is linked, so "Assigned To" can\'t be shown.',
            "recommended_correction": "Use an Administrator correction to set the correct status, "
            "or re-record the assignment/delivery.",
        }


def check_serialized_asset_without_serial(breadcrumbs):
    assets = UnitAsset.objects.select_related(
        "product", "product__brand", "current_location"
    ).filter(product__category=ItemCategory.SERIALIZED_ASSET, vendor_serial="")
    for asset in assets:
        country, location_label = _location_context(breadcrumbs, asset.current_location)
        yield {
            "issue_type": DataQualityIssueType.SERIALIZED_ASSET_WITHOUT_SERIAL,
            "severity": DataQualitySeverity.MEDIUM,
            "object_type": "UnitAsset",
            "object_id": str(asset.pk),
            "country": country,
            "location_label": location_label,
            "explanation": f"{asset.product} is categorized as a Serialized Asset but this unit "
            "has no serial number.",
            "recommended_correction": "Record the real serial number, or reclassify the product "
            "to Reusable Accessory/Component if it's genuinely never serialized.",
        }


def check_invalid_balance(breadcrumbs):
    balances = StockBalance.objects.select_related("product", "product__brand", "location").filter(
        product__is_active=False
    )
    for balance in balances:
        if balance.on_hand_quantity <= 0 and balance.reserved_quantity <= 0:
            continue
        country, location_label = _location_context(breadcrumbs, balance.location)
        yield {
            "issue_type": DataQualityIssueType.INVALID_BALANCE,
            "severity": DataQualitySeverity.MEDIUM,
            "object_type": "StockBalance",
            "object_id": str(balance.pk),
            "country": country,
            "location_label": location_label,
            "explanation": f"{balance.product} is deactivated but still carries "
            f"{balance.on_hand_quantity} on hand ({balance.reserved_quantity} reserved) at "
            f"{balance.location}.",
            "recommended_correction": "Reactivate the product if it's still genuinely stocked, or "
            "use an Administrator balance correction to zero it out.",
        }


def check_stale_custody_pointer(breadcrumbs):
    assets = (
        UnitAsset.objects.select_related("product", "product__brand")
        .filter(current_custody_transaction__isnull=False)
        .exclude(status__in=_CUSTODY_STATUSES)
    )
    for asset in assets:
        yield {
            "issue_type": DataQualityIssueType.STALE_CUSTODY_POINTER,
            "severity": DataQualitySeverity.MEDIUM,
            "object_type": "UnitAsset",
            "object_id": str(asset.pk),
            "country": "",
            "location_label": "",
            "explanation": f'Status is "{asset.get_status_display()}" but a custody (assignment/'
            "delivery) transaction is still linked — it should have been cleared on return/loss/"
            "disposal.",
            "recommended_correction": "Use an Administrator correction to re-save the current "
            "status, which clears the stale custody pointer.",
        }


def check_customer_stock_missing_reference(breadcrumbs):
    assets = UnitAsset.objects.select_related(
        "product", "product__brand", "current_location"
    ).filter(stock_purpose=StockPurpose.CUSTOMER, final_customer="", project_reference="")
    for asset in assets:
        country, location_label = _location_context(breadcrumbs, asset.current_location)
        yield {
            "issue_type": DataQualityIssueType.CUSTOMER_STOCK_MISSING_REFERENCE,
            "severity": DataQualitySeverity.MEDIUM,
            "object_type": "UnitAsset",
            "object_id": str(asset.pk),
            "country": country,
            "location_label": location_label,
            "explanation": "Stock purpose is Customer but neither a final customer nor a project "
            "reference is recorded.",
            "recommended_correction": "Fill in the final customer and/or project reference this "
            "stock belongs to.",
        }


def check_inactive_location_with_active_stock(breadcrumbs):
    assets = UnitAsset.objects.select_related(
        "product", "product__brand", "current_location"
    ).filter(current_location__is_active=False, status__in=_ON_BOOKS_STATUSES)
    for asset in assets:
        country, location_label = _location_context(breadcrumbs, asset.current_location)
        yield {
            "issue_type": DataQualityIssueType.INACTIVE_LOCATION_WITH_ACTIVE_STOCK,
            "severity": DataQualitySeverity.HIGH,
            "object_type": "UnitAsset",
            "object_id": str(asset.pk),
            "country": country,
            "location_label": location_label,
            "explanation": f"Located at {asset.current_location}, which is deactivated, while "
            f'still "{asset.get_status_display()}".',
            "recommended_correction": "Transfer this asset to an active location, or reactivate "
            "the location if it's genuinely still in use.",
        }

    balances = StockBalance.objects.select_related("product", "product__brand", "location").filter(
        location__is_active=False, on_hand_quantity__gt=0
    )
    for balance in balances:
        country, location_label = _location_context(breadcrumbs, balance.location)
        yield {
            "issue_type": DataQualityIssueType.INACTIVE_LOCATION_WITH_ACTIVE_STOCK,
            "severity": DataQualitySeverity.HIGH,
            "object_type": "StockBalance",
            "object_id": str(balance.pk),
            "country": country,
            "location_label": location_label,
            "explanation": f"{balance.product} has {balance.on_hand_quantity} on hand at "
            f"{balance.location}, which is deactivated.",
            "recommended_correction": "Transfer this stock to an active location, or reactivate "
            "the location if it's genuinely still in use.",
        }


def _expected_parent_level(level):
    index = Location.LEVEL_ORDER.index(level)
    return None if index == 0 else Location.LEVEL_ORDER[index - 1]


def check_invalid_location_hierarchy(breadcrumbs):
    """Expected to be near-empty going forward — apps.locations.services.
    create_location() already refuses to create a mismatched level/parent
    pair — this exists to surface legacy or imported locations from before
    that validation existed, not a rule this app itself can violate.

    Also flags a level outside the *current* Location.LEVEL_ORDER entirely
    (e.g. a leftover 'site'/'floor' row from before the hierarchy was
    collapsed to Country/Storage Room/Rack-Shelf) — apps.locations.
    migrations.0003_alter_location_level leaves exactly such a row in place,
    never deleted, when a historical ledger record still references it
    (ledger tables are append-only and can never be re-pointed to make room
    for the delete). _expected_parent_level() can't compute a real answer
    for a level it doesn't recognize, so this is reported directly instead
    of calling it.
    """
    for location in Location.objects.select_related("parent"):
        if location.level not in Location.LEVEL_ORDER:
            yield _hierarchy_finding(
                location,
                f"'{location.level}' is not a level in the current location hierarchy "
                "(Country/Storage Room/Rack-Shelf) — most likely a pre-collapse Site/Floor "
                "row kept only because a historical record still references it. No action "
                "needed unless you're specifically auditing that old reference.",
            )
            continue
        expected_parent_level = _expected_parent_level(location.level)
        if expected_parent_level is None:
            if location.parent is not None:
                yield _hierarchy_finding(location, "A Country must not have a parent location.")
        elif location.parent is None or location.parent.level != expected_parent_level:
            expected_label = dict(Location.Level.choices).get(
                expected_parent_level, expected_parent_level
            )
            yield _hierarchy_finding(
                location,
                f"A {location.get_level_display()} must be created under a {expected_label}.",
            )


def _hierarchy_finding(location, explanation):
    return {
        "issue_type": DataQualityIssueType.INVALID_LOCATION_HIERARCHY,
        "severity": DataQualitySeverity.HIGH,
        "object_type": "Location",
        "object_id": str(location.pk),
        "country": "",
        "location_label": str(location),
        "explanation": explanation,
        "recommended_correction": "Correct this location's parent, or archive it if it's a "
        "leftover from before hierarchy validation existed.",
    }


def check_missing_procurement_info(breadcrumbs):
    """Scoped to New-condition units only — a New item was presumably just
    purchased, so its supplier/invoice should be known; a Refurbished/Used/
    Damaged unit may have entered inventory (an internal transfer, a return)
    with no purchase paper trail of its own, which isn't a data problem.
    """
    assets = UnitAsset.objects.select_related(
        "product", "product__brand", "current_location"
    ).filter(condition=Condition.NEW, supplier="", invoice_number="")
    for asset in assets:
        country, location_label = _location_context(breadcrumbs, asset.current_location)
        yield {
            "issue_type": DataQualityIssueType.MISSING_PROCUREMENT_INFO,
            "severity": DataQualitySeverity.LOW,
            "object_type": "UnitAsset",
            "object_id": str(asset.pk),
            "country": country,
            "location_label": location_label,
            "explanation": "Condition is New but neither a supplier nor an invoice number is "
            "recorded.",
            "recommended_correction": "Fill in the supplier and/or invoice number from the "
            "purchase records, if available.",
        }


def check_duplicate_product(breadcrumbs):
    products = Product.objects.filter(is_active=True).select_related("brand", "product_type")
    groups = {}
    for product in products:
        groups.setdefault((product.brand_id, product.normalized_model), []).append(product)
    for group in groups.values():
        if len(group) < 2:
            continue
        for product in group:
            others = ", ".join(str(p) for p in group if p.pk != product.pk)
            yield {
                "issue_type": DataQualityIssueType.DUPLICATE_PRODUCT,
                "severity": DataQualitySeverity.LOW,
                "object_type": "Product",
                "object_id": str(product.pk),
                "country": "",
                "location_label": "",
                "explanation": f"Same brand and model as: {others}.",
                "recommended_correction": "Confirm these are genuinely different products, or "
                "merge by deactivating the redundant one(s) and moving their stock.",
            }


def check_orphaned_transaction_reference(breadcrumbs):
    """ "Orphaned transaction reference" here means ledger-consistency
    divergence, not a dangling foreign key (PROTECT everywhere makes a
    literal dangling FK unreachable): an asset whose live status/location
    doesn't match what its own most recent AssetStatusHistory line says it
    should be — the denormalized fields (apps.inventory.services.ledger's
    write_unit_line()) have drifted from the ledger's own record.

    The latest history row per asset is fetched via a correlated subquery
    (same pattern as apps.reporting.queries.reorder_suggestions()) rather
    than one query per asset — at 8,000+ assets that was 8,000+ queries
    inside the single @transaction.atomic block run_detection() wraps the
    whole scan in.
    """
    latest_history = AssetStatusHistory.objects.filter(unit_asset_id=OuterRef("pk")).order_by(
        "-transaction__occurred_at", "-transaction__created_at", "-occurred_at"
    )
    assets = UnitAsset.objects.select_related(
        "product", "product__brand", "current_location"
    ).annotate(
        latest_to_status=Subquery(latest_history.values("to_status")[:1]),
        latest_to_location_id=Subquery(latest_history.values("to_location_id")[:1]),
        latest_to_location_name=Subquery(latest_history.values("to_location__name")[:1]),
    )
    for asset in assets:
        if asset.latest_to_status is None:
            continue
        if (
            asset.latest_to_status != asset.status
            or asset.latest_to_location_id != asset.current_location_id
        ):
            country, location_label = _location_context(breadcrumbs, asset.current_location)
            latest_status_label = UnitStatus(asset.latest_to_status).label
            yield {
                "issue_type": DataQualityIssueType.ORPHANED_TRANSACTION_REFERENCE,
                "severity": DataQualitySeverity.HIGH,
                "object_type": "UnitAsset",
                "object_id": str(asset.pk),
                "country": country,
                "location_label": location_label,
                "explanation": f'Currently "{asset.get_status_display()}" at '
                f"{asset.current_location or '—'}, but its own last recorded ledger line says "
                f'"{latest_status_label}" at {asset.latest_to_location_name or "—"}.',
                "recommended_correction": "Investigate what changed this asset outside its own "
                "ledger history, then use an Administrator correction to reconcile it.",
            }


def check_country_only_location(breadcrumbs):
    """Flags stock recorded above Storage Room level — a Country (or a
    pre-collapse Site/Floor row 0003_alter_location_level.py had to leave in
    place because a historical ledger record still references it) is an
    authorization boundary, never a valid final stock location (spec: "do
    not guess locations"). Only ever finds pre-existing/legacy or imported
    data now: every write path (Add Stock, Transfer, Return, Repair, Admin
    Correction, import execution) enforces apps.locations.scoping.
    require_room_or_below() going forward, so this check exists purely to
    surface what's already wrong, never to guess or auto-move it — the
    correction is an ordinary Transfer (StockBalance) or Admin Correction/
    Transfer (UnitAsset) into a real Storage Room, both already audited.

    Excludes ROOM_OR_BELOW_LEVELS rather than listing "the levels above
    room" — deny-by-default, so a level outside the current scheme entirely
    (that orphaned Site/Floor row) is still caught, not silently treated as
    fine because it isn't literally 'country'.
    """
    assets = (
        UnitAsset.objects.select_related("product", "product__brand", "current_location")
        .filter(current_location__isnull=False)
        .exclude(current_location__level__in=ROOM_OR_BELOW_LEVELS)
    )
    for asset in assets:
        country, location_label = _location_context(breadcrumbs, asset.current_location)
        yield {
            "issue_type": DataQualityIssueType.COUNTRY_ONLY_LOCATION,
            "severity": DataQualitySeverity.HIGH,
            "object_type": "UnitAsset",
            "object_id": str(asset.pk),
            "country": country,
            "location_label": location_label,
            "explanation": f"Recorded at {asset.current_location} "
            f"({asset.current_location.get_level_display()}), not a Storage Room.",
            "recommended_correction": "Transfer this asset into a specific Storage Room (or "
            "correct it via an Administrator correction).",
        }

    balances = (
        StockBalance.objects.select_related("product", "product__brand", "location")
        .filter(on_hand_quantity__gt=0)
        .exclude(location__level__in=ROOM_OR_BELOW_LEVELS)
    )
    for balance in balances:
        country, location_label = _location_context(breadcrumbs, balance.location)
        yield {
            "issue_type": DataQualityIssueType.COUNTRY_ONLY_LOCATION,
            "severity": DataQualitySeverity.HIGH,
            "object_type": "StockBalance",
            "object_id": str(balance.pk),
            "country": country,
            "location_label": location_label,
            "explanation": f"{balance.on_hand_quantity} on hand at {balance.location} "
            f"({balance.location.get_level_display()}), not a Storage Room.",
            "recommended_correction": "Transfer this balance into a specific Storage Room.",
        }


# Order matters only for a predictable scan order in logs/tests, not for
# correctness — each check is independent and dedup_key-keyed.
ALL_CHECKS = [
    check_duplicate_serial,
    check_missing_location,
    check_missing_custodian,
    check_serialized_asset_without_serial,
    check_invalid_balance,
    check_stale_custody_pointer,
    check_customer_stock_missing_reference,
    check_inactive_location_with_active_stock,
    check_invalid_location_hierarchy,
    check_missing_procurement_info,
    check_duplicate_product,
    check_orphaned_transaction_reference,
    check_country_only_location,
]
