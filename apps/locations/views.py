from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.core.authorization import ADMINISTRATOR, RoleRequiredMixin

from .forms import LocationForm
from .models import Location
from .scoping import require_location_access, scope_queryset
from .services import create_location, deactivate_location, reactivate_location


class LocationListView(LoginRequiredMixin, ListView):
    model = Location
    template_name = "locations/location_list.html"
    context_object_name = "locations"
    paginate_by = 50

    def get_queryset(self):
        queryset = scope_queryset(
            self.request.user,
            Location.objects.select_related("parent"),
            location_field=None,
        )
        level = self.request.GET.get("level")
        if level:
            queryset = queryset.filter(level=level)
        if self.request.GET.get("show_inactive") != "1":
            queryset = queryset.filter(is_active=True)
        return queryset.order_by("level", "name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["levels"] = Location.Level.choices
        context["selected_level"] = self.request.GET.get("level", "")
        context["show_inactive"] = self.request.GET.get("show_inactive") == "1"
        return context


class LocationDetailView(LoginRequiredMixin, DetailView):
    model = Location
    template_name = "locations/location_detail.html"
    context_object_name = "location"

    def get_object(self, queryset=None):
        obj = get_object_or_404(Location.objects.select_related("parent"), pk=self.kwargs["pk"])
        require_location_access(self.request.user, obj)
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["ancestors"] = self.object.ancestors()
        context["children"] = scope_queryset(
            self.request.user, self.object.children.all(), location_field=None
        ).order_by("name")
        return context


class LocationCreateView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def get(self, request):
        initial = {}
        parent_id = request.GET.get("parent")
        if parent_id:
            initial["parent"] = parent_id
        form = LocationForm(initial=initial)
        return render(request, "locations/location_form.html", {"form": form})

    def post(self, request):
        form = LocationForm(request.POST)
        if not form.is_valid():
            return render(request, "locations/location_form.html", {"form": form})

        try:
            location = create_location(
                level=form.cleaned_data["level"],
                name=form.cleaned_data["name"],
                code=form.cleaned_data["code"],
                parent=form.cleaned_data["parent"],
                user=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, "locations/location_form.html", {"form": form})

        messages.success(request, f"Created {location.get_level_display()} '{location.name}'.")
        return redirect(location.get_absolute_url())


class LocationToggleActiveView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def get(self, request, pk):
        location = get_object_or_404(Location, pk=pk)
        return render(
            request, "locations/location_confirm_toggle_active.html", {"location": location}
        )

    def post(self, request, pk):
        location = get_object_or_404(Location, pk=pk)
        if location.is_active:
            deactivate_location(location=location, user=request.user)
            messages.success(request, f"Deactivated '{location.name}'.")
        else:
            reactivate_location(location=location, user=request.user)
            messages.success(request, f"Reactivated '{location.name}'.")
        return redirect(location.get_absolute_url())
