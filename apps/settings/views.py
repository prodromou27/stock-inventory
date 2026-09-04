from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views import View

from apps.core.authorization import ADMINISTRATOR, RoleRequiredMixin

from .forms import CertificateUploadForm, SystemSettingsForm
from .models import SystemSettings
from .services import update_certificate, update_system_settings


class SettingsHubView(LoginRequiredMixin, View):
    """Deliberately not role-gated (unlike every other view in this module):
    Settings is one of the app's top-level nav destinations, and it's also
    where Locations management now lives (templates/settings/hub.html) —
    Location viewing/editing has its own, wider permission story
    (apps.locations.views.LocationListView is open to any authenticated
    user; only create/edit/toggle-active require Administrator or
    StockManager, per docs/architecture's permission matrix), so a
    Read-Only user must still be able to reach this hub to see it. Every
    *other* card on this page links to a view that independently enforces
    its own stricter allowed_roles — hiding a card here is a UI nicety, not
    the actual authorization boundary, exactly like every other hub page in
    this app (movements_hub.html, etc.).
    """

    template_name = "settings/hub.html"

    def get(self, request):
        return render(request, self.template_name)


class SystemConfigurationView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "settings/system_form.html"

    def get(self, request):
        settings_obj = SystemSettings.load()
        form = SystemSettingsForm(
            initial={
                "site_name": settings_obj.site_name,
                "allowed_hosts_override": settings_obj.allowed_hosts_override,
            }
        )
        return render(request, self.template_name, {"form": form, "settings_obj": settings_obj})

    def post(self, request):
        form = SystemSettingsForm(request.POST, request.FILES)
        settings_obj = SystemSettings.load()
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "settings_obj": settings_obj})

        try:
            settings_obj = update_system_settings(
                user=request.user,
                site_name=form.cleaned_data["site_name"],
                allowed_hosts_override=form.cleaned_data["allowed_hosts_override"],
                logo=form.cleaned_data["logo"],
                remove_logo=form.cleaned_data["remove_logo"],
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
            return render(request, self.template_name, {"form": form, "settings_obj": settings_obj})

        messages.success(request, "System settings saved.")
        return redirect("settings:system")


class CertificateUploadView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "settings/certificates_form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": CertificateUploadForm()})

    def post(self, request):
        form = CertificateUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        try:
            update_certificate(
                user=request.user,
                cert_file=form.cleaned_data["cert_file"],
                key_file=form.cleaned_data["key_file"],
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
            return render(request, self.template_name, {"form": form})
        except OSError as exc:
            form.add_error(None, f"Could not write certificate files: {exc}")
            return render(request, self.template_name, {"form": form})

        messages.success(
            request,
            "Certificate saved. Run `docker compose -f deploy/docker-compose.prod.yml "
            "restart proxy` for it to take effect.",
        )
        return redirect("settings:certificates")
