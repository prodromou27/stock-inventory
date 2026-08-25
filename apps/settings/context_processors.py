from .models import SystemSettings


def branding_context(request):
    """Available on every page, authenticated or not — base.html's sidebar
    brand (authenticated) and auth-card brand (login) both need it. Reuses
    apps.settings.middleware.SystemSettingsMiddleware's lookup (stashed on
    the request) rather than querying the same row a second time.
    """
    settings_obj = getattr(request, "_system_settings", None) or SystemSettings.load()
    return {
        "site_name": settings_obj.site_name or "Stock Inventory",
        "site_logo_url": settings_obj.logo.url if settings_obj.logo else "",
    }
