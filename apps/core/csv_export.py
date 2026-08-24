import csv

from django.http import HttpResponse


class CSVExportMixin:
    """Add to a ListView: `?format=csv` streams a normalized CSV export of
    the full filtered/scoped queryset (not just the current page) instead of
    rendering the paginated HTML page — spec §15: "Exports use a new
    normalized format rather than reproducing the legacy Excel layout."

    Subclasses set `csv_filename`/`csv_headers` and implement
    `csv_rows(queryset)` (an iterable of row value lists).
    """

    csv_filename = "export.csv"
    csv_headers = []

    def csv_rows(self, queryset):
        raise NotImplementedError

    def render_to_response(self, context, **response_kwargs):
        if self.request.GET.get("format") == "csv":
            return self._csv_response()
        return super().render_to_response(context, **response_kwargs)

    def _csv_response(self):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{self.csv_filename}"'
        writer = csv.writer(response)
        writer.writerow(self.csv_headers)
        for row in self.csv_rows(self.get_queryset()):
            writer.writerow(row)
        return response
