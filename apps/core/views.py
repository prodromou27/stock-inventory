from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connection
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from .models import DASHBOARD_CARDS
from .services import dashboard_card_order, hidden_dashboard_cards, save_dashboard_cards

DASHBOARD_CARD_KEYS = {key for key, _ in DASHBOARD_CARDS}


class HealthCheckView(View):
    """Used by the Docker Compose healthcheck; also reachable directly for manual checks."""

    def get(self, request, *args, **kwargs):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            database_status = "ok"
        except OperationalError:
            database_status = "unavailable"

        status_code = 200 if database_status == "ok" else 503
        return JsonResponse(
            {"status": "ok" if database_status == "ok" else "error", "database": database_status},
            status=status_code,
        )


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = "core/home.html"

    def get_context_data(self, **kwargs):
        # Imported here, not at module level — apps.reporting depends on
        # apps.inventory/apps.locations, and apps.core is meant to stay the
        # dependency-free foundational layer everything else builds on
        # (apps.core.authorization's docstring); only this one view, not the
        # module itself, needs reporting's dashboard_summary().
        from apps.reporting.queries import dashboard_summary, recent_transactions

        from .recently_viewed import recently_viewed_for

        context = super().get_context_data(**kwargs)
        context["stats"] = dashboard_summary(self.request.user)
        context["recent_activity"] = recent_transactions(self.request.user)
        context["recently_viewed"] = recently_viewed_for(self.request.user)
        hidden = hidden_dashboard_cards(self.request.user)
        from apps.locations.models import Location
        from apps.locations.scoping import accessible_locations

        context["stock_rooms"] = (
            accessible_locations(self.request.user)
            .filter(
                level__in=[Location.Level.STORAGE_ROOM, Location.Level.RACK_SHELF], is_active=True
            )
            .select_related("parent")
            .order_by("name")
        )
        labels = dict(DASHBOARD_CARDS)
        urls = {
            "assets_in_stock": f"{reverse('inventory:asset_list')}?status=in_stock",
            "quantity_on_hand": reverse("inventory:balance_list"),
            "internal_stock_count": f"{reverse('inventory:asset_list')}?stock_purpose=internal",
            "customer_stock_count": f"{reverse('inventory:asset_list')}?stock_purpose=customer",
            "low_stock_count": reverse("reporting:low_stock"),
            "active_reservations": reverse("reporting:reserved_stock"),
            "assigned_count": f"{reverse('inventory:asset_list')}?status=assigned",
            "delivered_count": f"{reverse('inventory:asset_list')}?status=delivered",
            "damaged_count": reverse("reporting:damaged_assets"),
            "lost_count": reverse("reporting:lost_assets"),
            "disposed_count": reverse("reporting:disposed_items"),
            "recent_transactions": reverse("inventory:transaction_list"),
        }
        alert_keys = {"low_stock_count", "damaged_count", "lost_count"}
        context["dashboard_cards"] = [
            {
                "key": key,
                "label": labels[key],
                "value": context["stats"][key],
                "url": urls[key],
                "alert": key in alert_keys and bool(context["stats"][key]),
            }
            for key in dashboard_card_order(self.request.user)
            if key not in hidden
        ]
        context["visible_dashboard_cards"] = [card["key"] for card in context["dashboard_cards"]]
        return context


class DashboardPreferenceView(LoginRequiredMixin, View):
    """Lets any logged-in user pick which Dashboard stat cards they see —
    reached from the dashboard itself and from the Settings hub. A plain
    checkbox list rather than a ModelForm: DASHBOARD_CARDS (not the model)
    is the source of truth for which keys are valid, so an unchecked box
    just means "add this key to hidden_cards," with no separate form-field
    declaration to keep in sync as cards are added or removed.
    """

    template_name = "core/dashboard_preferences_form.html"

    def get(self, request):
        hidden = hidden_dashboard_cards(request.user)
        labels = dict(DASHBOARD_CARDS)
        return render(
            request,
            self.template_name,
            {
                "cards": [
                    {"key": key, "label": labels[key], "visible": key not in hidden}
                    for key in dashboard_card_order(request.user)
                ]
            },
        )

    def post(self, request):
        checked = set(request.POST.getlist("visible_cards")) & DASHBOARD_CARD_KEYS
        save_dashboard_cards(request.user, checked, request.POST.getlist("card_order"))
        messages.success(request, "Dashboard preferences saved.")
        return redirect("core:home")


SEARCH_RESULT_LIMIT = 15
SUGGEST_RESULT_LIMIT = 5
# Below this TrigramSimilarity score a match is noise, not a real typo-tolerant
# hit — icontains (exact substring) is still ORed in below regardless of score,
# so a correctly-spelled short query never depends on this threshold.
TRIGRAM_THRESHOLD = 0.15


def _asset_match_reason(asset, query):
    if asset.matched_employee:
        return f"Employee: {asset.matched_employee}"
    if asset.matched_customer:
        return f"Customer: {asset.matched_customer}"
    query = query.casefold()
    checks = (
        (asset.vendor_serial, "Serial number"),
        (asset.name, "Asset name"),
        (asset.product.brand.name, "Brand"),
        (asset.product.model, "Model"),
        (asset.product.sku, "SKU"),
        (asset.product.product_type.name, "Product type"),
        (asset.project_reference, "Project reference"),
        (asset.final_customer, "Customer"),
    )
    return next(
        (label for value, label in checks if query in (value or "").casefold()), "Product details"
    )


def _search_results(user, query, limit, offset=0):
    """Shared by GlobalSearchView (the full results page) and
    SearchSuggestView (the topbar's live-preview dropdown) — same querysets,
    same scoping, same ranking, just a different result cap and presentation.
    Every result set is built from the same scoped queryset each list view
    already uses — never a fresh unscoped query — so a Stock Manager
    searching never sees a product/asset/transaction outside their granted
    locations. Product metadata is searched through physical assets, so the
    results answer "which items?" rather than returning catalog definitions.

    Ranks by Postgres trigram similarity (apps.core.migrations.
    0002_enable_pg_trgm + the GIN trigram indexes on Brand/Product/UnitAsset)
    so a misspelled/partial query still surfaces close matches, ordered by
    how close — plain icontains is still ORed into the filter for exact
    substrings, since icontains alone can't use a trigram index (Django
    #32803) and a short exact query can score below TRIGRAM_THRESHOLD.
    Transaction numbers are exact, system-generated IDs (TXN-000047), not a
    fuzzy-search target, so that result set stays on plain icontains.
    """
    # Imported here, not at module level, for the same reason HomeView's
    # dashboard_summary import is local — apps.core stays dependency-free.
    from django.contrib.postgres.search import TrigramSimilarity
    from django.db import connection
    from django.db.models import Exists, OuterRef, Q, Subquery

    from apps.inventory.access import scope_asset_queryset, scope_transaction_queryset
    from apps.inventory.models import InventoryTransaction, InventoryTransactionLine, UnitAsset

    if not query:
        return {"assets": [], "transactions": []}

    # Summing three TrigramSimilarity() calls and filtering on the total
    # (the previous approach) can't use any of the GIN trigram indexes on
    # these fields — Postgres has to compute similarity for every row. The
    # `%` operator (Django's __trigram_similar lookup) is what the index
    # actually accelerates, so that's the filter now; TrigramSimilarity
    # stays, unchanged, purely for order_by() ranking of the already-
    # narrowed result. `%` reads its threshold from the pg_trgm.
    # similarity_threshold session GUC rather than taking one per call, so
    # it's set here to match TRIGRAM_THRESHOLD — safe to set on every
    # search request since no other query in this app uses a trigram
    # operator at a different threshold.
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_limit(%s)", [TRIGRAM_THRESHOLD])

    recipient_history = InventoryTransactionLine.objects.filter(unit_asset_id=OuterRef("pk"))
    recipient_history_match = recipient_history.filter(
        Q(transaction__employee_name__icontains=query)
        | Q(transaction__final_customer__icontains=query)
    )
    matching_employee = (
        recipient_history.exclude(transaction__employee_name="")
        .filter(transaction__employee_name__icontains=query)
        .order_by("-transaction__occurred_at", "-transaction__created_at")
    )
    matching_customer = (
        recipient_history.exclude(transaction__final_customer="")
        .filter(transaction__final_customer__icontains=query)
        .order_by("-transaction__occurred_at", "-transaction__created_at")
    )
    asset_queryset = (
        scope_asset_queryset(
            user,
            UnitAsset.objects.select_related(
                "product",
                "product__brand",
                "product__product_type",
                "current_location",
                "current_custody_transaction",
            ),
        )
        .annotate(
            _recipient_history_match=Exists(recipient_history_match),
            matched_employee=Subquery(matching_employee.values("transaction__employee_name")[:1]),
            matched_customer=Subquery(matching_customer.values("transaction__final_customer")[:1]),
        )
        .filter(
            Q(normalized_serial__trigram_similar=query.upper())
            | Q(product__model__trigram_similar=query)
            | Q(product__sku__trigram_similar=query)
            | Q(product__brand__name__trigram_similar=query)
            | Q(project_reference__trigram_similar=query)
            | Q(final_customer__trigram_similar=query)
            | Q(normalized_serial__icontains=query.upper())
            | Q(name__icontains=query)
            | Q(product__brand__name__icontains=query)
            | Q(product__model__icontains=query)
            | Q(product__sku__icontains=query)
            | Q(product__product_type__name__icontains=query)
            | Q(project_reference__icontains=query)
            | Q(final_customer__icontains=query)
            | Q(current_custody_transaction__employee_name__icontains=query)
            | Q(_recipient_history_match=True)
        )
        .annotate(
            similarity=TrigramSimilarity("normalized_serial", query.upper())
            + TrigramSimilarity("product__model", query)
            + TrigramSimilarity("product__sku", query)
            + TrigramSimilarity("product__brand__name", query)
            + TrigramSimilarity("project_reference", query)
            + TrigramSimilarity("final_customer", query)
        )
        .order_by("-similarity", "-created_at")
    )
    assets = list(asset_queryset[offset : offset + limit])
    for asset in assets:
        asset.search_match_reason = _asset_match_reason(asset, query)
    transaction_queryset = (
        scope_transaction_queryset(
            user,
            InventoryTransaction.objects.select_related("performed_by"),
        )
        .filter(
            Q(transaction_number__icontains=query)
            | Q(project_reference__icontains=query)
            | Q(final_customer__icontains=query)
            | Q(employee_name__icontains=query)
        )
        .order_by("-occurred_at", "-created_at")
    )
    transactions = list(transaction_queryset[offset : offset + limit])
    return {"assets": assets, "transactions": transactions}


class GlobalSearchView(LoginRequiredMixin, TemplateView):
    """Top-bar search box in base.html — the full results page reached by
    submitting the box or clicking "See all results" in SearchSuggestView's
    dropdown. See _search_results() for the scoping/ranking this is built on.
    """

    template_name = "core/search_results.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        try:
            page_number = max(1, int(self.request.GET.get("page", "1")))
        except ValueError:
            page_number = 1
        offset = (page_number - 1) * SEARCH_RESULT_LIMIT
        context["query"] = query
        results = _search_results(self.request.user, query, SEARCH_RESULT_LIMIT + 1, offset)
        context["has_next"] = any(
            len(results[key]) > SEARCH_RESULT_LIMIT for key in ("assets", "transactions")
        )
        context["has_previous"] = page_number > 1
        context["search_page"] = page_number
        context["assets"] = results["assets"][:SEARCH_RESULT_LIMIT]
        context["transactions"] = results["transactions"][:SEARCH_RESULT_LIMIT]
        return context


class SearchSuggestView(LoginRequiredMixin, View):
    """JSON endpoint behind static/js/search.js's live-preview dropdown under
    the topbar search box — debounced-fetched as the operator types, so they
    can jump straight to a result without a full page load. Same
    scoped/ranked result sets as GlobalSearchView, just capped smaller and
    serialized to plain JSON instead of rendered to HTML.
    """

    def get(self, request, *args, **kwargs):
        query = request.GET.get("q", "").strip()
        results = _search_results(request.user, query, SUGGEST_RESULT_LIMIT)

        def rows(kind):
            for obj in results[kind]:
                label = str(obj)
                if kind == "assets":
                    label = f"{label} — {obj.search_match_reason}"
                yield {"label": label, "url": obj.get_absolute_url()}

        return JsonResponse(
            {
                "query": query,
                "assets": list(rows("assets")),
                "transactions": list(rows("transactions")),
            }
        )
