"""Click-to-sort support for ListView-based grids — shared by every list
view that wants it (apps.inventory.views.UnitAssetListView was the first,
building this out as a reusable mixin once four more views needed the same
~15 lines). See templates/_sort_th.html / apps.core.templatetags.ui_extras.
sort_th for the template side.
"""


class SortableListMixin:
    """Set `sort_fields = {"key": "model__field__path", ...}` (an explicit
    allow-list — never a raw user-supplied field path) and `default_ordering`
    (a tuple passed to order_by() when ?sort= is absent/unrecognized, and
    appended as a stable tiebreaker when it isn't). Call
    `self.apply_sort(queryset)` from get_queryset().
    """

    sort_fields = {}
    default_ordering = ()

    def apply_sort(self, queryset):
        sort_key = self.request.GET.get("sort")
        if sort_key not in self.sort_fields:
            return queryset.order_by(*self.default_ordering)
        field = self.sort_fields[sort_key]
        if self.request.GET.get("dir") == "desc":
            field = f"-{field}"
        return queryset.order_by(field, *self.default_ordering)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["sort_key"] = self.request.GET.get("sort", "")
        context["sort_dir"] = self.request.GET.get("dir", "asc")
        return context


def parse_multi_sort(params):
    """Parses repeated `?sort=field:dir` params (the data-grid JS's multi-
    column-sort request format — apps.inventory.views' *GridDataView, static/
    js/inventory_grid.js) into a plain `[(key, dir), ...]` list. Malformed
    entries are skipped rather than raising — a stray/hand-edited querystring
    should degrade to "unsorted on that entry", not 500.
    """
    sorters = []
    for raw in params.getlist("sort"):
        key, _, direction = raw.partition(":")
        if not key:
            continue
        sorters.append((key, "desc" if direction == "desc" else "asc"))
    return sorters


def positive_int_param(value, default):
    """Shared by every grid JSON endpoint's page/size params (apps.inventory.
    views.*GridDataView, apps.catalog.views.ProductGridDataView) — never lets
    a malformed or non-positive value through instead of just falling back.
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def apply_multi_sort(queryset, sort_fields, sorters, default_ordering=()):
    """The multi-column equivalent of SortableListMixin.apply_sort() — same
    explicit allow-list contract (`sort_fields`, `{"key": "orm__path"}`),
    but accepts several (key, dir) pairs (see parse_multi_sort()) instead of
    one, applied in the order given via a single order_by() call so ties on
    the first sorter break on the second, and so on. Unrecognized keys are
    silently dropped (same fail-safe as the single-sort version) rather than
    reaching into an arbitrary ORM path from user input.
    """
    fields = [
        f"-{sort_fields[key]}" if direction == "desc" else sort_fields[key]
        for key, direction in sorters
        if key in sort_fields
    ]
    if not fields:
        return queryset.order_by(*default_ordering)
    return queryset.order_by(*fields, *default_ordering)
