"""Presentation-only template helpers for static/css/app.css's badge styles.

Heuristic, not a lookup against every TextChoices enum in the codebase — new
status values fall back to badge--neutral rather than needing this file
updated every time a choices list changes elsewhere.
"""

from django import template

register = template.Library()

_SECTION_LABELS = {
    "accounts": "Access management",
    "audit": "Audit log",
    "catalog": "Product catalog",
    "core": "Dashboard",
    "dataquality": "Data Quality Centre",
    "documents": "Documents",
    "exports": "Exports",
    "imports": "Excel import",
    "inventory": "Inventory",
    "locations": "Locations",
    "reporting": "Reports",
    "settings": "Settings",
}

# Views inside the inventory app that the sidebar files under their own
# entry (Operations / Transactions) — labelled to match, so the breadcrumb
# and topbar don't say "Inventory" while the sidebar highlights another item.
_URL_NAME_SECTION_LABELS = {
    "movements_hub": "Operations",
    "receive_stock": "Operations",
    "quick_receive": "Operations",
    "receive_bulk": "Operations",
    "transfer": "Operations",
    "reserve": "Operations",
    "reservation_list": "Operations",
    "reservation_detail": "Operations",
    "assign": "Operations",
    "deliver": "Operations",
    "assess_return": "Operations",
    "mark_damaged": "Operations",
    "repair_damaged": "Operations",
    "mark_lost": "Operations",
    "dispose": "Operations",
    "put_in_use": "Operations",
    "remove_from_use": "Operations",
    "transaction_list": "Transactions",
    "transaction_detail": "Transactions",
    "transaction_reverse": "Transactions",
    "return_stock": "Transactions",
}

# Movement type -> badge tone (apps.inventory.models.MovementType values).
# Stock coming in reads as success, stock leaving custody as info, exceptions
# as warning/danger, and administrative bookkeeping as neutral.
_MOVEMENT_BADGES = {
    "receipt": "badge--success",
    "remove_from_use": "badge--success",
    "return": "badge--success",
    "assignment": "badge--info",
    "delivery": "badge--info",
    "put_in_use": "badge--info",
    "reservation": "badge--info",
    "transfer": "badge--neutral",
    "reservation_release": "badge--neutral",
    "return_assessment": "badge--neutral",
    "purpose_change": "badge--neutral",
    "install_component": "badge--neutral",
    "remove_component": "badge--neutral",
    "mark_damaged": "badge--warning",
    "correction": "badge--warning",
    "reversal": "badge--warning",
    "mark_lost": "badge--danger",
    "disposal": "badge--danger",
}

_SUCCESS = (
    "active",
    "available",
    "in_stock",
    "in stock",
    "new",
    "good",
    "healthy",
    "completed",
    "succeeded",
)
_INFO = (
    "reserved",
    "assigned",
    "in_use",
    "in use",
    "in_transit",
    "in transit",
    "pending",
    "processing",
    "queued",
    "running",
)
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


@register.filter
def movement_badge_class(movement_type):
    """Badge tone for an InventoryTransaction.movement_type code."""
    return _MOVEMENT_BADGES.get(str(movement_type or ""), "badge--neutral")


@register.filter
def thousands(value):
    """8025 -> "8,025" for integer counts; anything else passes through."""
    if isinstance(value, bool) or not isinstance(value, int):
        return value
    return f"{value:,}"


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


@register.inclusion_tag("_sort_th.html", takes_context=True)
def sort_th(context, sort_value, label):
    """A <th> that links to sorting the current list by `sort_value` (an
    entry in the view's SORT_FIELDS allow-list), toggling direction on
    repeat clicks — used with a view that sets sort_key/sort_dir in its
    context (see apps.inventory.views.UnitAssetListView). Only meaningful
    inside a template that also has `request` in context (base.html always
    does), since it renders a {% querystring %} link to preserve filters.
    """
    sort_key = context.get("sort_key", "")
    sort_dir = context.get("sort_dir", "asc")
    is_active = sort_key == sort_value
    return {
        "request": context.get("request"),
        "sort_value": sort_value,
        "label": label,
        "next_dir": "desc" if (is_active and sort_dir == "asc") else "asc",
        "arrow": ("▲" if sort_dir == "asc" else "▼") if is_active else "",
    }


@register.inclusion_tag("_pagination.html", takes_context=True)
def render_pagination(context):
    """The `is_paginated`/`page_obj` Previous/Next `<nav>` block every
    ListView-backed list template hand-copied identically (21 sites) —
    `{% render_pagination %}` replaces the whole block, reading directly
    from context so no arguments are needed at call sites.
    """
    return {
        "is_paginated": context.get("is_paginated"),
        "page_obj": context.get("page_obj"),
        "request": context.get("request"),
    }


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


@register.simple_tag(takes_context=True)
def current_section(context):
    """Human-friendly current area label for the shared header/breadcrumb."""
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    if match is None:
        return "Invenio"
    if match.app_name == "inventory" and match.url_name in _URL_NAME_SECTION_LABELS:
        return _URL_NAME_SECTION_LABELS[match.url_name]
    return _SECTION_LABELS.get(match.app_name, (match.app_name or "Invenio").title())
