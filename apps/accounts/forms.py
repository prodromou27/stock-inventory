from django import forms
from django.contrib.auth import get_user_model

from apps.locations.models import Location

User = get_user_model()


class GrantAccessForm(forms.Form):
    user = forms.ModelChoiceField(queryset=User.objects.order_by("username"))
    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True, level=Location.Level.COUNTRY).order_by(
            "name"
        ),
        help_text="Country access automatically includes all locations below it.",
    )
