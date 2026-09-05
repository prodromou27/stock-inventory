from .models import SystemSettings


def _darken(hex_color, amount=0.12):
    """A flat percentage darken of a #rrggbb color — enough to give
    base.html's --color-primary-hover override a visibly distinct shade
    without asking the Administrator to pick two colors for one setting.
    """
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    r, g, b = (max(0, round(channel * (1 - amount))) for channel in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


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
        "accent_color": settings_obj.accent_color,
        "accent_color_hover": (
            _darken(settings_obj.accent_color) if settings_obj.accent_color else ""
        ),
    }
