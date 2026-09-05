import csv
import json

from django.http import HttpResponse


class CSVExportMixin:
    """Add to a ListView: `?format=csv` streams a normalized CSV export of
    the full filtered/scoped queryset (not just the current page) instead of
    rendering the paginated HTML page — spec §15: "Exports use a new
    normalized format rather than reproducing the legacy Excel layout."

    Subclasses set `csv_filename`/`csv_headers` and implement
    `csv_rows(queryset)` (an iterable of row value lists) for the default,
    fixed-column export.

    A subclass that also implements `csv_row_dict(obj)` (a field-name ->
    value dict, one row) additionally supports "Export Current View": a
    `columns` GET param — JSON `[{field, title, visible}, ...]`, the exact
    shape static/js/inventory_grid.js's captureGridState().columns already
    produces from Tabulator's own getColumnLayout() — picks and orders the
    exported columns to match whatever the grid is showing right now,
    instead of the fixed csv_headers/csv_rows shape. `csv_non_exportable_fields`
    excludes structural dict keys (an id, a detail URL, ...) that aren't
    real columns even though they're present in csv_row_dict()'s output.
    """

    csv_filename = "export.csv"
    csv_headers = []
    csv_non_exportable_fields = frozenset()

    def csv_rows(self, queryset):
        raise NotImplementedError

    def csv_row_dict(self, obj):
        """Optional — see the class docstring's "Export Current View" note.
        Returning None (the default) means this view only ever supports the
        fixed csv_headers/csv_rows export, never the dynamic ?columns= one.
        """
        return None

    def render_to_response(self, context, **response_kwargs):
        if self.request.GET.get("format") == "csv":
            return self._csv_response()
        return super().render_to_response(context, **response_kwargs)

    def _requested_columns(self):
        raw = self.request.GET.get("columns")
        if not raw:
            return None
        try:
            requested = json.loads(raw)
        except (TypeError, ValueError):
            return None
        columns = [
            (col["field"], col.get("title") or col["field"])
            for col in requested
            if isinstance(col, dict)
            and col.get("field")
            and col.get("visible", True)
            and col["field"] not in self.csv_non_exportable_fields
        ]
        return columns or None

    def _csv_response(self):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{self.csv_filename}"'
        writer = csv.writer(response)
        queryset = self.get_queryset()
        # Bulk "Export selected" from a grid's cross-page checkbox
        # selection (static/js/inventory_grid.js's selectedById) — narrows
        # the already-scoped/filtered queryset to exactly these rows rather
        # than whatever's currently in the header filters, so exporting a
        # specific selection never depends on the grid's filter state.
        ids = self.request.GET.getlist("ids")
        if ids:
            queryset = queryset.filter(pk__in=ids)

        supports_dynamic_columns = type(self).csv_row_dict is not CSVExportMixin.csv_row_dict
        columns = self._requested_columns() if supports_dynamic_columns else None
        if columns is not None:
            writer.writerow([title for _, title in columns])
            for obj in queryset.iterator():
                row = self.csv_row_dict(obj) or {}
                writer.writerow([row.get(field) or "" for field, _ in columns])
            return response

        writer.writerow(self.csv_headers)
        for row in self.csv_rows(queryset):
            writer.writerow(row)
        return response
