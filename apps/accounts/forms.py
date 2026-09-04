from django import forms
from django.contrib.auth import get_user_model, password_validation

from apps.core.authorization import ADMINISTRATOR, READ_ONLY_USER, STOCK_MANAGER
from apps.locations.models import Location

User = get_user_model()

ROLE_CHOICES = [
    (ADMINISTRATOR, "Administrator"),
    (STOCK_MANAGER, "Stock Manager"),
    (READ_ONLY_USER, "Read-only"),
]


class GrantAccessForm(forms.Form):
    user = forms.ModelChoiceField(queryset=User.objects.order_by("username"))
    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True, level=Location.Level.COUNTRY).order_by(
            "name"
        ),
        help_text="Country access automatically includes all locations below it.",
    )


class CreateUserForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(
        widget=forms.PasswordInput,
        help_text="At least 12 characters. The new user must change this on first login.",
    )
    role = forms.ChoiceField(choices=ROLE_CHOICES)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

    def clean_password(self):
        password = self.cleaned_data["password"]
        # Validated against a throwaway unsaved User (username may not be
        # cleaned yet if this runs first — password_validation only needs
        # the value itself for the similarity check, which degrades
        # gracefully against an empty username).
        password_validation.validate_password(
            password, User(username=self.data.get("username", ""))
        )
        return password


class SetUserRoleForm(forms.Form):
    role = forms.ChoiceField(choices=ROLE_CHOICES)
