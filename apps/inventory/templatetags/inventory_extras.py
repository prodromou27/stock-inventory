"""Template-level composition so templates/catalog/product_detail.html can
show inventory data (stock by location, recent movement activity) on a
product page without apps.catalog.views ever importing apps.inventory —
docs/architecture/01-repository-structure.md's app dependency table allows
`inventory` to depend on `catalog` (it already does, for Product), never
the reverse; a template {% load %} keeps that direction intact, since it's
the template doing the composing, not apps.catalog's own Python code.
"""

from django import template
from django.db.models import Count

from apps.catalog.models import TrackingMethod
from apps.locations.scoping import scope_queryset

from ..access import scope_transaction_line_queryset
from ..models import InventoryTransactionLine, StockBalance, UnitAsset, UnitStatus

register = template.Library()


@register.inclusion_tag("inventory/_product_summary.html", takes_context=True)
def product_inventory_summary(context, product):
    """Everything scoped exactly the way apps.inventory's own list views
    already scope it (scope_queryset()/scope_transaction_line_queryset())
    — a Stock Manager sees only their accessible locations' stock and
    activity for this product, never the full picture an Administrator
    would see.
    """
    user = context["request"].user
    is_unit_tracked = product.tracking_method == TrackingMethod.UNIT

    if is_unit_tracked:
        stock_by_location = list(
            scope_queryset(
                user,
                UnitAsset.objects.filter(product=product, status=UnitStatus.IN_STOCK),
                location_field="current_location",
            )
            .values("current_location__name")
            .annotate(count=Count("id"))
            .order_by("current_location__name")
        )
    else:
        stock_by_location = list(
            scope_queryset(
                user, StockBalance.objects.filter(product=product), location_field="location"
            )
            .select_related("location")
            .order_by("location__name")
        )

    recent_activity = list(
        scope_transaction_line_queryset(
            user,
            InventoryTransactionLine.objects.filter(product=product).select_related("transaction"),
        ).order_by("-transaction__occurred_at", "-transaction__created_at")[:10]
    )

    return {
        "is_unit_tracked": is_unit_tracked,
        "stock_by_location": stock_by_location,
        "recent_activity": recent_activity,
    }
