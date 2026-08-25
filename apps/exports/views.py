from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views import View

from apps.core.authorization import ADMINISTRATOR, RoleRequiredMixin

from .forms import ExportSettingsForm
from .models import ExportSettings
from .services import run_export, update_settings


class ExportSettingsView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "exports/settings.html"

    def get(self, request):
        settings_obj = ExportSettings.load()
        form = ExportSettingsForm(
            initial={
                "export_path": settings_obj.export_path,
                "schedule": settings_obj.schedule,
                "weekly_weekday": settings_obj.weekly_weekday,
            }
        )
        return render(request, self.template_name, {"form": form, "settings_obj": settings_obj})

    def post(self, request):
        form = ExportSettingsForm(request.POST)
        settings_obj = ExportSettings.load()
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "settings_obj": settings_obj})

        try:
            settings_obj = update_settings(
                user=request.user,
                export_path=form.cleaned_data["export_path"],
                schedule=form.cleaned_data["schedule"],
                weekly_weekday=int(form.cleaned_data["weekly_weekday"]),
            )
        except ValidationError as exc:
            form.add_error(
                "export_path" if "path" in str(exc).lower() else None, "; ".join(exc.messages)
            )
            return render(request, self.template_name, {"form": form, "settings_obj": settings_obj})

        messages.success(request, "Export settings saved.")
        return redirect("exports:settings")


class RunExportNowView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request):
        try:
            path = run_export(user=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        except OSError as exc:
            messages.error(request, f"Export failed: {exc}")
        else:
            messages.success(request, f"Export written to {path}.")
        return redirect("exports:settings")
