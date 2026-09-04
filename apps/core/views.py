from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connection
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView


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
        from apps.reporting.queries import (
            dashboard_summary,
            data_quality_summary,
            recent_transactions,
        )

        from .recently_viewed import recently_viewed_for

        context = super().get_context_data(**kwargs)
        context["stats"] = dashboard_summary(self.request.user)
        context["data_quality"] = data_quality_summary(self.request.user)
        context["recent_activity"] = recent_transactions(self.request.user)
        context["recently_viewed"] = recently_viewed_for(self.request.user)
        return context


SEARCH_RESULT_LIMIT = 15
SUGGEST_RESULT_LIMIT = 5
# Below this TrigramSimilarity score a match is noise, not a real typo-tolerant
# hit — icontains (exact substring) is still ORed in below regardless of score,
# so a correctly-spelled short query never depends on this threshold.
TRIGRAM_THRESHOLD = 0.15


def _search_results(user, query, limit):
    """Shared by GlobalSearchView (the full results page) and
    SearchSuggestView (the topbar's live-preview dropdown) — same querysets,
    same scoping, same ranking, just a different result cap and presentation.
    Every result set is built from the same scoped queryset each list view
    already uses — never a fresh unscoped query — so a Stock Manager
    searching never sees a product/asset/transaction outside their granted
    locations. Products are catalog-global (not location-scoped, per
    docs/architecture/01-repository-structure.md), so they're the one result
    set without a scope filter.

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
    from django.db.models import Q

    from apps.catalog.models import Product
    from apps.inventory.access import scope_transaction_queryset
    from apps.inventory.models import InventoryTransaction, UnitAsset
    from apps.locations.scoping import scope_queryset

    if not query:
        return {"products": [], "assets": [], "transactions": []}

    products = list(
        Product.objects.select_related("brand", "product_type")
        .filter(is_active=True)
        .annotate(
            similarity=TrigramSimilarity("model", query)
            + TrigramSimilarity("sku", query)
            + TrigramSimilarity("brand__name", query)
        )
        .filter(
            Q(similarity__gt=TRIGRAM_THRESHOLD)
            | Q(brand__name__icontains=query)
            | Q(model__icontains=query)
            | Q(sku__icontains=query)
            | Q(product_type__name__icontains=query)
        )
        .order_by("-similarity", "brand__name", "model")[:limit]
    )
    assets = list(
        scope_queryset(
            user,
            UnitAsset.objects.select_related("product", "product__brand", "current_location"),
            location_field="current_location",
        )
        .annotate(
            similarity=TrigramSimilarity("normalized_serial", query.upper())
            + TrigramSimilarity("project_reference", query)
            + TrigramSimilarity("final_customer", query)
        )
        .filter(
            Q(similarity__gt=TRIGRAM_THRESHOLD)
            | Q(normalized_serial__icontains=query.upper())
            | Q(project_reference__icontains=query)
            | Q(final_customer__icontains=query)
        )
        .order_by("-similarity", "-created_at")[:limit]
    )
    transactions = list(
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
        .order_by("-occurred_at", "-created_at")[:limit]
    )
    return {"products": products, "assets": assets, "transactions": transactions}


class GlobalSearchView(LoginRequiredMixin, TemplateView):
    """Top-bar search box in base.html — the full results page reached by
    submitting the box or clicking "See all results" in SearchSuggestView's
    dropdown. See _search_results() for the scoping/ranking this is built on.
    """

    template_name = "core/search_results.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        context["query"] = query
        context.update(_search_results(self.request.user, query, SEARCH_RESULT_LIMIT))
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
                yield {"label": str(obj), "url": obj.get_absolute_url()}

        return JsonResponse(
            {
                "query": query,
                "products": list(rows("products")),
                "assets": list(rows("assets")),
                "transactions": list(rows("transactions")),
            }
        )
