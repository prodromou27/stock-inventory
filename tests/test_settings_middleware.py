import pytest
from django.conf import settings as django_settings
from django.test import RequestFactory
from django.utils import timezone

from apps.settings.middleware import SystemSettingsMiddleware, _default_allowed_hosts
from apps.settings.models import SystemSettings


def _run_middleware():
    middleware = SystemSettingsMiddleware(get_response=lambda request: "ok")
    request = RequestFactory().get("/")
    middleware(request)
    return request


@pytest.mark.django_db
class TestSystemSettingsMiddleware:
    """Asserts against apps.settings.middleware._default_allowed_hosts (the
    module-level snapshot the middleware itself reverts to) rather than a
    freshly re-captured django_settings.ALLOWED_HOSTS — that global is
    mutated by every request in the whole suite (any test hitting any view
    runs this middleware), so re-capturing it fresh inside one test is
    order-dependent on whatever a sibling test left it as; the module
    constant is the actual, order-independent contract being tested.
    """

    def test_no_settings_row_leaves_allowed_hosts_at_the_default(self, settings):
        _run_middleware()
        assert django_settings.ALLOWED_HOSTS == _default_allowed_hosts

    def test_override_replaces_allowed_hosts(self, settings):
        SystemSettings.objects.create(pk=1, allowed_hosts_override="only.example.com")
        _run_middleware()
        assert django_settings.ALLOWED_HOSTS == ["only.example.com"]

    def test_clearing_the_override_reverts_to_the_default(self, settings):
        SystemSettings.objects.create(pk=1, allowed_hosts_override="only.example.com")
        _run_middleware()
        assert django_settings.ALLOWED_HOSTS == ["only.example.com"]

        settings_obj = SystemSettings.load()
        settings_obj.allowed_hosts_override = ""
        settings_obj.save()
        _run_middleware()
        assert django_settings.ALLOWED_HOSTS == _default_allowed_hosts

    def test_stashes_the_loaded_settings_on_the_request(self, settings):
        request = _run_middleware()
        assert hasattr(request, "_system_settings")
        assert isinstance(request._system_settings, SystemSettings)

    def test_configured_timezone_is_activated(self, settings):
        SystemSettings.objects.create(pk=1, timezone="America/New_York")
        try:
            _run_middleware()
            assert timezone.get_current_timezone_name() == "America/New_York"
        finally:
            timezone.deactivate()

    def test_blank_timezone_falls_back_to_the_server_default(self, settings):
        timezone.activate("Asia/Tokyo")
        try:
            SystemSettings.objects.create(pk=1, timezone="")
            _run_middleware()
            assert timezone.get_current_timezone_name() == django_settings.TIME_ZONE
        finally:
            timezone.deactivate()
