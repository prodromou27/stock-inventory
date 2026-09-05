import re
import zoneinfo

from django import forms

from apps.locations.models import Location, LocationLevel

from .models import NotificationSubscription

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class SystemSettingsForm(forms.Form):
    site_name = forms.CharField(
        max_length=100,
        required=False,
        label="Site name",
        help_text="Shown in the sidebar and browser tab. Leave blank to use “Stock Inventory”.",
    )
    accent_color = forms.CharField(
        max_length=7,
        required=False,
        label="Brand color",
        help_text="Used for primary buttons, links, and focus rings app-wide. Leave blank for "
        "the built-in default blue.",
        widget=forms.TextInput(attrs={"type": "color"}),
    )
    allowed_hosts_override = forms.CharField(
        max_length=1000,
        required=False,
        label="Allowed hosts override",
        help_text=(
            "Comma-separated hostnames (e.g. inventory.example.com). Leave blank to keep the "
            "ALLOWED_HOSTS value from .env.production. Takes effect immediately — a wrong "
            "value can lock you out of the site (see the help text on the model / "
            "docs/architecture/09-delivery-backlog.md for the recovery command)."
        ),
    )
    logo = forms.FileField(
        required=False, label="Logo (PNG or JPEG) — leave blank to keep the current one"
    )
    remove_logo = forms.BooleanField(required=False, label="Remove the current logo")

    def clean_accent_color(self):
        value = self.cleaned_data["accent_color"]
        if value and not _HEX_COLOR_RE.match(value):
            raise forms.ValidationError("Enter a color as #rrggbb.")
        return value.lower()


class CertificateUploadForm(forms.Form):
    cert_file = forms.FileField(label="Certificate (fullchain.pem)")
    key_file = forms.FileField(label="Private key (privkey.pem)")


class TimezoneSettingsForm(forms.Form):
    timezone = forms.ChoiceField(
        choices=[("", "Server default")]
        + sorted((zone, zone) for zone in zoneinfo.available_timezones()),
        required=False,
        label="Business timezone",
        help_text='Drives "today" for arrival dates and every business-date default across '
        "the app. Leave blank to use the server's configured default.",
        widget=forms.Select(attrs={"data-filterable": "true"}),
    )


class SmtpSettingsForm(forms.Form):
    """Stores outgoing-email configuration and, optionally, sends one test
    message via apps.settings.services.send_test_email() right after saving
    — see that function's docstring for why a failed test send doesn't roll
    back the save itself.
    """

    smtp_host = forms.CharField(max_length=255, required=False, label="SMTP host")
    smtp_port = forms.IntegerField(
        required=False, min_value=1, max_value=65535, label="SMTP port", initial=587
    )
    smtp_username = forms.CharField(max_length=255, required=False, label="Username")
    smtp_password = forms.CharField(
        max_length=255,
        required=False,
        label="Password",
        widget=forms.PasswordInput(render_value=True),
    )
    smtp_use_tls = forms.BooleanField(required=False, initial=True, label="Use TLS")
    smtp_from_email = forms.CharField(max_length=255, required=False, label="From address")
    test_email_recipient = forms.EmailField(
        required=False,
        label="Send a test email to (optional)",
        help_text="Leave blank to just save the settings without sending anything.",
    )


class NotificationSubscriptionForm(forms.ModelForm):
    class Meta:
        model = NotificationSubscription
        fields = [
            "recipient",
            "country",
            "is_active",
            "notify_low_stock",
            "notify_overdue_assignments",
            "notify_import_export_failures",
            "notify_data_quality",
        ]
        labels = {
            "notify_import_export_failures": "Import and export failures",
            "notify_data_quality": "Unresolved high-severity data-quality findings",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["country"].queryset = Location.objects.filter(
            level=LocationLevel.COUNTRY, is_active=True
        ).order_by("name")
        self.fields["recipient"].queryset = (
            self.fields["recipient"].queryset.filter(is_active=True).order_by("username")
        )

    def clean_recipient(self):
        recipient = self.cleaned_data["recipient"]
        if not recipient.email:
            raise forms.ValidationError("The recipient must have an email address.")
        return recipient
