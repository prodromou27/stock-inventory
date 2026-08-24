from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView

from apps.core.authorization import ADMINISTRATOR, RoleRequiredMixin

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
        if after := self.request.GET.get("after", "").strip():
            queryset = queryset.filter(occurred_at__date__gte=after)
        if before := self.request.GET.get("before", "").strip():
            queryset = queryset.filter(occurred_at__date__lte=before)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["event_types"] = AuditEvent.EventType.choices
        context["selected_event_type"] = self.request.GET.get("event_type", "")
        return context
