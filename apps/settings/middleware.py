from django.conf import settings as django_settings

# Captured once, at process start (module import), before any request can
# mutate django_settings.ALLOWED_HOSTS below — this is what a blank
# SystemSettings.allowed_hosts_override reverts to.
_default_allowed_hosts = list(django_settings.ALLOWED_HOSTS)


class SystemSettingsMiddleware:
    """Applies SystemSettings.allowed_hosts_override (if any) to
    django.conf.settings.ALLOWED_HOSTS before host-header validation runs.

    Must be first in MIDDLEWARE: in production SECURE_SSL_REDIRECT=True
    makes SecurityMiddleware call request.get_host() — which validates
    against ALLOWED_HOSTS — on every request, so this has to run before
    SecurityMiddleware to have any effect. A blank override is a no-op:
    ALLOWED_HOSTS reverts to the env-configured default captured above.

    Self-lockout is possible if an Administrator sets this to a value that
    doesn't include the hostname they're accessing the site through — see
    SystemSettings.allowed_hosts_override's help_text for the recovery
    command (clear the row directly via `manage.py shell`, same pattern as
    clearing an axes lockout — CLAUDE.md).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .models import SystemSettings

        # Stashed on the request so context_processors.branding_context can
        # reuse this same lookup instead of issuing a second query for the
        # same row on every request (tests/test_performance.py's asset-list
        # query-count budget is tight enough that this matters).
        request._system_settings = SystemSettings.load()
        override = request._system_settings.allowed_hosts_list
        django_settings.ALLOWED_HOSTS = override or list(_default_allowed_hosts)
        return self.get_response(request)
