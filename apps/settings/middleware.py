from django.conf import settings as django_settings
from django.utils import timezone

# Captured once, at process start (module import), before any request can
# mutate django_settings.ALLOWED_HOSTS below — this is what a blank
# SystemSettings.allowed_hosts_override reverts to.
_default_allowed_hosts = list(django_settings.ALLOWED_HOSTS)


class SystemSettingsMiddleware:
    """Applies SystemSettings.allowed_hosts_override (if any) to
    django.conf.settings.ALLOWED_HOSTS before host-header validation runs,
    and activates SystemSettings.timezone (if any) as this request's active
    timezone.

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

    timezone.activate()/deactivate() only affect *this* request's thread —
    unlike the ALLOWED_HOSTS assignment above, there's no global-mutation
    race to worry about. A blank SystemSettings.timezone deactivates any
    previously-active zone so Django falls back to settings.TIME_ZONE, the
    documented default (django.utils.timezone.get_current_timezone()'s own
    behavior when nothing has been activated).

    Every management command that runs outside a request — currently only
    apps.exports.management.commands.run_scheduled_export, invoked by cron
    — has no middleware pipeline at all and must call timezone.activate()
    itself; see that command's own docstring.
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
        if request._system_settings.timezone:
            timezone.activate(request._system_settings.timezone)
        else:
            timezone.deactivate()
        return self.get_response(request)
