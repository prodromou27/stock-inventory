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
