from django import forms

from apps.inventory.models import StockPurpose
from apps.locations.models import ROOM_OR_BELOW_LEVELS, Location, order_by_hierarchy

from .parsing import MAX_IMPORT_SIZE_BYTES

# Imports are Administrator-only (apps.imports.views' RoleRequiredMixin), so
# these querysets need no per-user accessible_locations() scoping — only the
# level restriction every stock-holding location field enforces: a Country
# is an authorization boundary, never a valid final stock location.


class ImportUploadForm(forms.Form):
    file = forms.FileField(label="Excel (.xlsx) or CSV (.csv) file")
    default_location = forms.ModelChoiceField(
        queryset=order_by_hierarchy(
            Location.objects.filter(is_active=True, level__in=ROOM_OR_BELOW_LEVELS)
        ),
        required=False,
        label="Default location (optional)",
        help_text="Used for any row whose LOCATION column doesn't resolve. Rows with their own "
        "resolvable location are unaffected.",
    )
    default_stock_purpose = forms.ChoiceField(
        choices=StockPurpose.choices,
        required=False,
        initial=StockPurpose.INTERNAL,
        label="Default stock purpose",
        help_text="Used for any row without its own Stock Purpose column value.",
    )

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        name = (uploaded.name or "").lower()
        if not (name.endswith(".xlsx") or name.endswith(".csv")):
            raise forms.ValidationError("Only .xlsx or .csv files are supported.")
        if uploaded.size > MAX_IMPORT_SIZE_BYTES:
            raise forms.ValidationError("Import files must be 25 MB or smaller.")
        return uploaded

    def clean(self):
        cleaned = super().clean()
        cleaned["default_stock_purpose"] = (
            cleaned.get("default_stock_purpose") or StockPurpose.INTERNAL
        )
        return cleaned


class RowLocationOverrideForm(forms.Form):
    location = forms.ModelChoiceField(queryset=Location.objects.none(), label="Correct location")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = order_by_hierarchy(
            Location.objects.filter(is_active=True, level__in=ROOM_OR_BELOW_LEVELS)
        )
