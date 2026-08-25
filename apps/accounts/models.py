from django.conf import settings
from django.db import models


class UserLocationAccess(models.Model):
    """Grants `user` access to `location` and everything under it
    (docs/architecture/02-data-model.md, docs/architecture/04-permission-matrix.md).
    Administrators don't need grant rows — see apps.core.authorization.is_administrator.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="location_access_grants",
    )
    location = models.ForeignKey(
        "locations.Location",
        on_delete=models.PROTECT,
        related_name="user_access_grants",
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
    )
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "location"], name="unique_user_location_access"
            ),
        ]
        ordering = ["user__username", "location__name"]

    def __str__(self):
        return f"{self.user} @ {self.location}"


class MustChangePassword(models.Model):
    """Presence of a row means `user` is still on a bootstrap-assigned
    default password and is blocked from doing anything else until they set
    a real one (apps.accounts.middleware.RequirePasswordChangeMiddleware,
    apps.accounts.views.ForcedPasswordChangeView) — see
    apps/accounts/management/commands/bootstrap_admin.py and
    docs/architecture/04-permission-matrix.md's "Default admin bootstrap"
    section for the full design and the security trade-off it documents.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="must_change_password"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} must change password"
