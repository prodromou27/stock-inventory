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
        from apps.reporting.queries import dashboard_summary

        context = super().get_context_data(**kwargs)
        context["stats"] = dashboard_summary(self.request.user)
        return context
