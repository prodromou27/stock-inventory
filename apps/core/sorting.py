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
