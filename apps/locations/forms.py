from django import forms

from .models import Location


class LocationForm(forms.Form):
    """A plain Form, not a ModelForm — creation goes through
    services.create_location() so validation/audit logic lives in one place.
    """

    level = forms.ChoiceField(choices=Location.Level.choices)
    name = forms.CharField(max_length=120)
    code = forms.CharField(max_length=30, required=False)
    parent = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True).order_by("level", "name"),
        required=False,
    )
