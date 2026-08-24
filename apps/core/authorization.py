"""Role checks (Django Group membership). Location-scope checks live in
apps.locations.scoping, which depends on this module — kept separate so
`core` itself stays dependency-free, per docs/architecture/01-repository-structure.md.
"""

from django.core.exceptions import PermissionDenied

ADMINISTRATOR = "Administrator"
STOCK_MANAGER = "StockManager"
READ_ONLY_USER = "ReadOnlyUser"


def is_administrator(user):
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name=ADMINISTRATOR).exists()
    )


def has_role(user, *group_names):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=group_names).exists()


def require_role(user, *group_names):
    if not has_role(user, *group_names):
        raise PermissionDenied("You do not have permission to perform this action.")


class RoleRequiredMixin:
    """Class-based-view mixin. Set `allowed_roles = (...)` and combine with
    django.contrib.auth.mixins.LoginRequiredMixin (role implies nothing about
    authentication on its own).
    """

    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        require_role(request.user, *self.allowed_roles)
        return super().dispatch(request, *args, **kwargs)
