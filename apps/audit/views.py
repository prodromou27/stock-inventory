from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import NoReverseMatch, reverse
from django.views.generic import DetailView, ListView

from apps.core.authorization import ADMINISTRATOR, RoleRequiredMixin
from apps.core.dates import parse_date_param

from .models import AuditEvent


class AuditLogListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    """The "Audit log" screen (spec §14) — Administrator-only (spec §4:
    only Administrators "view the complete audit log").
    """

    allowed_roles = (ADMINISTRATOR,)
    model = AuditEvent
    template_name = "audit/audit_log_list.html"
    context_object_name = "events"
    paginate_by = 50

    def get_queryset(self):
        queryset = AuditEvent.objects.select_related("actor").order_by("-occurred_at")

        if event_type := self.request.GET.get("event_type", "").strip():
            queryset = queryset.filter(event_type=event_type)
        if actor := self.request.GET.get("actor", "").strip():
            queryset = queryset.filter(actor__username__icontains=actor)
        if object_type := self.request.GET.get("object_type", "").strip():
            queryset = queryset.filter(object_type__icontains=object_type)
        if object_id := self.request.GET.get("object_id", "").strip():
            queryset = queryset.filter(object_id=object_id)
        if after := parse_date_param(self.request.GET.get("after", "").strip()):
            queryset = queryset.filter(occurred_at__date__gte=after)
        if before := parse_date_param(self.request.GET.get("before", "").strip()):
            queryset = queryset.filter(occurred_at__date__lte=before)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["event_types"] = AuditEvent.EventType.choices
        context["selected_event_type"] = self.request.GET.get("event_type", "")
        return context


class AuditEventDetailView(LoginRequiredMixin, RoleRequiredMixin, DetailView):
    allowed_roles = (ADMINISTRATOR,)
    model = AuditEvent
    template_name = "audit/audit_event_detail.html"
    context_object_name = "event"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        routes = {
            "UnitAsset": "inventory:asset_detail",
            "InventoryTransaction": "inventory:transaction_detail",
            "Product": "catalog:product_detail",
            "Location": "locations:detail",
            "GeneratedDocument": "documents:document_detail",
        }
        route = routes.get(self.object.object_type)
        if route and self.object.object_id:
            try:
                context["object_url"] = reverse(route, args=[self.object.object_id])
            except NoReverseMatch:
                pass
        return context
