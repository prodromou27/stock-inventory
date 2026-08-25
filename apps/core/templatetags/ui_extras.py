"""Presentation-only template helpers for static/css/app.css's badge styles.

Heuristic, not a lookup against every TextChoices enum in the codebase — new
status values fall back to badge--neutral rather than needing this file
updated every time a choices list changes elsewhere.
"""

from django import template

register = template.Library()

_SUCCESS = ("active", "available", "in_stock", "in stock", "new", "good", "healthy", "completed")
_INFO = ("reserved", "assigned", "in_transit", "in transit", "pending", "processing")
_WARNING = ("damaged", "fair", "returned")
_DANGER = ("lost", "disposed", "inactive", "error", "failed", "rejected", "cancelled")


@register.filter
def badge_class(value):
    text = str(value or "").strip().lower()
    if any(keyword in text for keyword in _SUCCESS):
        return "badge--success"
    if any(keyword in text for keyword in _INFO):
        return "badge--info"
    if any(keyword in text for keyword in _WARNING):
        return "badge--warning"
    if any(keyword in text for keyword in _DANGER):
        return "badge--danger"
    return "badge--neutral"


@register.simple_tag(takes_context=True)
def nav_active(context, *url_names):
    """ "is-active" when the current view's url_name is one of url_names —
    for sidebar links that group several related views (e.g. all of
    Movements' sub-forms) under a single nav entry.
    """
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    return "is-active" if match.url_name in url_names else ""


@register.simple_tag(takes_context=True)
def nav_active_app(context, *app_names):
    """ "is-active" when the current view's URLconf app_name is one of
    app_names — for sidebar links that map 1:1 onto a whole app.
    """
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    return "is-active" if match.app_name in app_names else ""
