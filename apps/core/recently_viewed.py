"""record_recently_viewed()/recently_viewed_for() — the Dashboard's
"Recently viewed" panel. See apps.core.models.RecentlyViewed's docstring
for why this is a generic (content_type/object_id) model living in
apps.core rather than a concrete FK per tracked type.
"""

from django.contrib.contenttypes.models import ContentType

from .models import RecentlyViewed


def record_recently_viewed(*, user, obj):
    """Called from the three tracked detail views (apps.catalog.views.
    ProductDetailView, apps.inventory.views.UnitAssetDetailView/
    TransactionDetailView) on every GET of a real (non-fragment) page —
    never for the grid side-panel's AJAX partial render, so opening the
    panel while scanning a grid doesn't flood this list the way visiting
    the full page does. A no-op for anonymous requests (shouldn't happen
    behind LoginRequiredMixin, but this stays safe to call unconditionally
    regardless).
    """
    if not user.is_authenticated:
        return
    content_type = ContentType.objects.get_for_model(obj)
    row, created = RecentlyViewed.objects.get_or_create(
        user=user, content_type=content_type, object_id=obj.pk
    )
    if not created:
        row.save()  # touches viewed_at (auto_now) without a second query


# content_type.model -> a friendly, fixed label — deliberately not derived
# from the model's own verbose_name (e.g. "unit asset"), so this list reads
# the same short way ("Asset") no matter how a model's Meta changes.
_TYPE_LABELS = {
    "product": "Product",
    "unitasset": "Asset",
    "inventorytransaction": "Transaction",
}


def recently_viewed_for(user, limit=8):
    """[{"type_label", "object", "viewed_at"}, ...] for the Dashboard's
    "Recently viewed" panel — resolving content_object here (once, in
    Python) rather than leaving the template to dereference the
    GenericForeignKey lazily per-row, and silently dropping any row whose
    target no longer resolves (shouldn't happen given this app's
    deactivate/append-only deletion policy, but a stale generic relation
    degrading to "just don't show it" is safer than a broken link).
    """
    rows = RecentlyViewed.objects.filter(user=user).select_related("content_type")[:limit]
    result = []
    for row in rows:
        target = row.content_object
        if target is None:
            continue
        result.append(
            {
                "type_label": _TYPE_LABELS.get(row.content_type.model, row.content_type.model),
                "object": target,
                "viewed_at": row.viewed_at,
            }
        )
    return result
