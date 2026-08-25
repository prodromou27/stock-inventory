from django.shortcuts import redirect
from django.urls import reverse

# Exact paths (not prefixes with trailing content) a user with a pending
# forced password change may still reach — the change-password page itself,
# logout (so they aren't trapped if they'd rather come back later), and
# static assets (so the change-password page actually renders).
_EXEMPT_PATH_PREFIXES = ("/static/",)


class RequirePasswordChangeMiddleware:
    """Blocks every authenticated request except the exemptions above while
    a MustChangePassword row exists for the user — a hard, server-enforced
    redirect, not a dismissible banner (docs/architecture/04-permission-matrix.md's
    "Default admin bootstrap" section: "never rely on hidden UI as security"
    applies here too). Only ever relevant for the docker compose up "single
    command install" bootstrap Administrator (apps.accounts.management.
    commands.bootstrap_admin) — everyone else never has this row.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            from .models import MustChangePassword

            # One cheap, indexed point-lookup (OneToOneField) per authenticated
            # request — negligible, and only ever non-empty for the bootstrap
            # Administrator until they change their password. Checked before
            # _is_exempt() so the URL-reversing there only runs when it matters.
            if MustChangePassword.objects.filter(
                user=request.user
            ).exists() and not self._is_exempt(request.path):
                return redirect(reverse("password_change"))
        return self.get_response(request)

    def _is_exempt(self, path):
        exempt_exact = {reverse("password_change"), reverse("logout")}
        if path in exempt_exact:
            return True
        return any(path.startswith(prefix) for prefix in _EXEMPT_PATH_PREFIXES)
