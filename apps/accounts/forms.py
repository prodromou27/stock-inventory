from django import forms
from django.contrib.auth import get_user_model, password_validation

from apps.core.authorization import ADMINISTRATOR, READ_ONLY_USER, STOCK_MANAGER
from apps.locations.forms import LocationChoiceField
from apps.locations.models import Location, order_by_hierarchy

User = get_user_model()

ROLE_CHOICES = [
    (ADMINISTRATOR, "Administrator"),
    (STOCK_MANAGER, "Stock Manager"),
    (READ_ONLY_USER, "Read-only"),
]


class GrantAccessForm(forms.Form):
    """`location` accepts any active location, any level — not just Country.
    The backend (grant_location_access()/apps.locations.scoping.scope_queryset())
    already scopes correctly at any granularity via path__descendant_or_self;
    this form used to hard-restrict the picker to Country-level only, which
    meant granting someone access to a single Storage Room (a normal,
    supported scenario — see tests.conftest.stock_manager_with_room_access)
    was only possible by calling grant_location_access() directly, never
    through this screen.
    """

    user = forms.ModelChoiceField(queryset=User.objects.order_by("username"))
    location = LocationChoiceField(
        queryset=order_by_hierarchy(Location.objects.filter(is_active=True)),
        help_text="Access to a location automatically includes everything below it — e.g. a "
        "Country grant includes every site and room beneath it; a Storage Room grant includes "
        "its racks and shelves.",
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
