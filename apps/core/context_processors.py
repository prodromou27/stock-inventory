from .authorization import is_administrator


def role_context(request):
    """Presentation only — every view that guards an action still enforces it
    server-side independently (docs/architecture/04-permission-matrix.md).
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {
        "user_role_groups": list(user.groups.values_list("name", flat=True)),
        "user_is_administrator": is_administrator(user),
    }
