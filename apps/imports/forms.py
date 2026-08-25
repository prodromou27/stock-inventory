from django import forms

from apps.locations.models import Location


class ImportUploadForm(forms.Form):
    file = forms.FileField(label="Excel (.xlsx) or CSV (.csv) file")

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        name = (uploaded.name or "").lower()
        if not (name.endswith(".xlsx") or name.endswith(".csv")):
            raise forms.ValidationError("Only .xlsx or .csv files are supported.")
        return uploaded


class RowLocationOverrideForm(forms.Form):
    location = forms.ModelChoiceField(queryset=Location.objects.none(), label="Correct location")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = Location.objects.filter(is_active=True).order_by(
            "level", "name"
        )
