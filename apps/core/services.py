"""Personal dashboard preferences; these never change stock access."""

from .models import DASHBOARD_CARDS, DashboardPreference

DEFAULT_CARDS = {"assets_in_stock", "quantity_on_hand", "assigned_count", "delivered_count"}


def hidden_dashboard_cards(user):
    saved = (
        DashboardPreference.objects.filter(user=user).values_list("hidden_cards", flat=True).first()
    )
    if saved is not None:
        return set(saved)
    return {key for key, _ in DASHBOARD_CARDS if key not in DEFAULT_CARDS}


def dashboard_card_order(user):
    valid = [key for key, _ in DASHBOARD_CARDS]
    saved = (
        DashboardPreference.objects.filter(user=user).values_list("card_order", flat=True).first()
    ) or []
    ordered = [key for key in saved if key in valid]
    return ordered + [key for key in valid if key not in ordered]


def save_dashboard_cards(user, selected, order=None):
    hidden = [key for key, _ in DASHBOARD_CARDS if key not in set(selected)]
    valid = [key for key, _ in DASHBOARD_CARDS]
    order = [key for key in (order or []) if key in valid]
    order += [key for key in valid if key not in order]
    DashboardPreference.objects.update_or_create(
        user=user, defaults={"hidden_cards": hidden, "card_order": order}
    )
