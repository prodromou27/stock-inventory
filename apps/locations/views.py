from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, RoleRequiredMixin
from apps.core.sorting import SortableListMixin

from .forms import LocationEditForm, LocationForm
from .models import Location
from .scoping import require_location_access, scope_queryset
from .services import (
    can_manage_location,
    create_location,
    deactivate_location,
    reactivate_location,
    update_location,
)


class LocationListView(LoginRequiredMixin, SortableListMixin, ListView):
    model = Location
    template_name = "locations/location_list.html"
    context_object_name = "locations"
    paginate_by = 50

    sort_fields = {
        "name": "name",
        "level": "level",
        "parent": "parent__name",
        "status": "is_active",
    }
    default_ordering = ("level", "name")

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
        return self.apply_sort(queryset)

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
        context["can_manage_location"] = can_manage_location(self.request.user, self.object)
        if (
            self.request.user.groups.filter(name=STOCK_MANAGER).exists()
            and not self.request.user.is_superuser
        ):
            context["can_add_child"] = self.object.level in (
                Location.Level.FLOOR,
                Location.Level.RACK_CABINET,
            )
        else:
            context["can_add_child"] = self.object.level != Location.Level.SHELF_BIN
        return context


class LocationCreateView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def get(self, request):
        initial = {}
        parent_id = request.GET.get("parent")
        if parent_id:
            initial["parent"] = parent_id
        form = LocationForm(initial=initial, user=request.user)
        return render(request, "locations/location_form.html", {"form": form})

    def post(self, request):
        if (
            not request.user.is_superuser
            and request.user.groups.filter(name=STOCK_MANAGER).exists()
            and request.POST.get("level")
            not in (Location.Level.STORAGE_ROOM, Location.Level.SHELF_BIN)
        ):
            raise PermissionDenied("Stock Managers may create storage rooms and shelves only.")
        form = LocationForm(request.POST, user=request.user)
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


class LocationEditView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def _get_location(self, request, pk):
        location = get_object_or_404(Location, pk=pk)
        if not can_manage_location(request.user, location):
            raise PermissionDenied("You cannot edit this location.")
        return location

    def get(self, request, pk):
        location = self._get_location(request, pk)
        form = LocationEditForm(initial={"name": location.name, "code": location.code})
        return render(request, "locations/location_form.html", {"form": form, "location": location})

    def post(self, request, pk):
        location = self._get_location(request, pk)
        form = LocationEditForm(request.POST)
        if form.is_valid():
            try:
                update_location(location=location, user=request.user, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Updated '{location.name}'.")
                return redirect(location.get_absolute_url())
        return render(request, "locations/location_form.html", {"form": form, "location": location})


class LocationToggleActiveView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)

    def _get_location(self, request, pk):
        location = get_object_or_404(Location, pk=pk)
        if not can_manage_location(request.user, location):
            raise PermissionDenied("You cannot change this location.")
        return location

    def get(self, request, pk):
        location = self._get_location(request, pk)
        return render(
            request, "locations/location_confirm_toggle_active.html", {"location": location}
        )

    def post(self, request, pk):
        location = self._get_location(request, pk)
        if location.is_active:
            deactivate_location(location=location, user=request.user)
            messages.success(request, f"Deactivated '{location.name}'.")
        else:
            reactivate_location(location=location, user=request.user)
            messages.success(request, f"Reactivated '{location.name}'.")
        return redirect(location.get_absolute_url())
