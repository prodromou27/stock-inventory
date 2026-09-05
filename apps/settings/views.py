from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views import View

from apps.core.authorization import ADMINISTRATOR, RoleRequiredMixin

from .forms import (
    CertificateUploadForm,
    NotificationSubscriptionForm,
    SmtpSettingsForm,
    SystemSettingsForm,
    TimezoneSettingsForm,
)
from .models import NotificationSubscription, SystemSettings
from .notifications import save_notification_subscription
from .services import (
    send_test_email,
    update_certificate,
    update_smtp_settings,
    update_system_settings,
    update_timezone_settings,
)


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
                "accent_color": settings_obj.accent_color,
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
                accent_color=form.cleaned_data["accent_color"],
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


class TimezoneConfigurationView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "settings/timezone_form.html"

    def get(self, request):
        settings_obj = SystemSettings.load()
        form = TimezoneSettingsForm(initial={"timezone": settings_obj.timezone})
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = TimezoneSettingsForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        try:
            update_timezone_settings(user=request.user, timezone=form.cleaned_data["timezone"])
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
            return render(request, self.template_name, {"form": form})

        messages.success(request, "Timezone saved.")
        return redirect("settings:timezone")


class SmtpConfigurationView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "settings/smtp_form.html"

    def get(self, request):
        settings_obj = SystemSettings.load()
        form = SmtpSettingsForm(
            initial={
                "smtp_host": settings_obj.smtp_host,
                "smtp_port": settings_obj.smtp_port,
                "smtp_username": settings_obj.smtp_username,
                "smtp_password": settings_obj.smtp_password,
                "smtp_use_tls": settings_obj.smtp_use_tls,
                "smtp_from_email": settings_obj.smtp_from_email,
            }
        )
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = SmtpSettingsForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        data = form.cleaned_data
        try:
            update_smtp_settings(
                user=request.user,
                smtp_host=data["smtp_host"],
                smtp_port=data["smtp_port"],
                smtp_username=data["smtp_username"],
                smtp_password=data["smtp_password"],
                smtp_use_tls=data["smtp_use_tls"],
                smtp_from_email=data["smtp_from_email"],
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
            return render(request, self.template_name, {"form": form})

        messages.success(request, "SMTP settings saved.")
        if data["test_email_recipient"]:
            try:
                send_test_email(recipient=data["test_email_recipient"])
            except Exception as exc:  # broad: any SMTP/network failure, reported as-is
                messages.error(request, f"Settings saved, but the test email failed: {exc}")
            else:
                messages.success(request, f"Test email sent to {data['test_email_recipient']}.")
        return redirect("settings:smtp")


class NotificationSubscriptionListView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "settings/notification_subscriptions.html"

    def get(self, request):
        subscriptions = NotificationSubscription.objects.select_related("recipient", "country")
        return render(
            request,
            self.template_name,
            {"subscriptions": subscriptions, "form": NotificationSubscriptionForm()},
        )

    def post(self, request):
        form = NotificationSubscriptionForm(request.POST)
        subscriptions = NotificationSubscription.objects.select_related("recipient", "country")
        if form.is_valid():
            try:
                save_notification_subscription(user=request.user, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
            else:
                messages.success(request, "Notification subscription created.")
                return redirect("settings:notifications")
        return render(
            request,
            self.template_name,
            {"subscriptions": subscriptions, "form": form},
        )


class NotificationSubscriptionUpdateView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "settings/notification_subscription_form.html"

    def _subscription(self, pk):
        from django.shortcuts import get_object_or_404

        return get_object_or_404(NotificationSubscription, pk=pk)

    def get(self, request, pk):
        subscription = self._subscription(pk)
        return render(
            request,
            self.template_name,
            {
                "subscription": subscription,
                "form": NotificationSubscriptionForm(instance=subscription),
            },
        )

    def post(self, request, pk):
        subscription = self._subscription(pk)
        form = NotificationSubscriptionForm(request.POST, instance=subscription)
        if form.is_valid():
            try:
                save_notification_subscription(
                    user=request.user, subscription=subscription, **form.cleaned_data
                )
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
            else:
                messages.success(request, "Notification subscription saved.")
                return redirect("settings:notifications")
        return render(request, self.template_name, {"subscription": subscription, "form": form})
