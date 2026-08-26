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
