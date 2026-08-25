from django import forms


class SystemSettingsForm(forms.Form):
    site_name = forms.CharField(
        max_length=100,
        required=False,
        label="Site name",
        help_text="Shown in the sidebar and browser tab. Leave blank to use “Stock Inventory”.",
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


class CertificateUploadForm(forms.Form):
    cert_file = forms.FileField(label="Certificate (fullchain.pem)")
    key_file = forms.FileField(label="Private key (privkey.pem)")
