from .authorization import STOCK_MANAGER, is_administrator


def role_context(request):
    """Presentation only — every view that guards an action still enforces it
    server-side independently (docs/architecture/04-permission-matrix.md).
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    roles = list(user.groups.values_list("name", flat=True))
    return {
        "user_role_groups": roles,
        "user_is_administrator": is_administrator(user),
        "user_is_stock_manager": user.is_superuser or STOCK_MANAGER in roles,
    }


def notification_bell(request):
    """The topbar bell's initial state — the last 10 unread and the total
    unread count, server-rendered on every page (no JS/AJAX needed, matching
    this app's existing preference for plain server-rendered UI wherever a
    live-polling dropdown isn't specifically worth the added complexity —
    see templates/base.html's equally-JS-free `user-menu`). One small
    indexed query (recipient, read_at) per authenticated request, the same
    order of cost as role_context() above.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    # Imported here, not at module level — apps.core stays dependency-free
    # of apps.settings, matching how apps.core.views already does this for
    # its own cross-app imports (apps.catalog/apps.inventory/apps.locations).
    from apps.settings.models import Notification

    # One query, not a list-slice plus a separate count(): the number of
    # unread rows here is inherently small (bounded by ~4 digest categories
    # times however many country subscriptions exist, never by inventory
    # scale), unlike the asset/product grids this app actually paginates —
    # fetching the lot and slicing in Python is cheaper than two queries.
    unread = list(
        Notification.objects.filter(recipient=user, read_at__isnull=True).select_related("country")
    )
    return {
        "unread_notifications": unread[:10],
        "unread_notification_count": len(unread),
    }
