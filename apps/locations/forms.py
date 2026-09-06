from django import forms

from .models import Location
from .scoping import accessible_locations

MANAGER_LEVELS = (
    (Location.Level.STORAGE_ROOM, Location.Level.STORAGE_ROOM.label),
    (Location.Level.RACK_CABINET, Location.Level.RACK_CABINET.label),
    (Location.Level.SHELF_BIN, Location.Level.SHELF_BIN.label),
)


class LocationForm(forms.Form):
    """A plain Form, not a ModelForm — creation goes through
    services.create_location() so validation/audit logic lives in one place.
    """

    level = forms.ChoiceField(choices=Location.Level.choices)
    name = forms.CharField(max_length=120)
    code = forms.CharField(max_length=30, required=False)
    parent = forms.ModelChoiceField(
        queryset=Location.objects.none(),
        required=False,
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.core.authorization import is_administrator

        parents = Location.objects.filter(is_active=True)
        if is_administrator(user):
            self.fields["parent"].queryset = parents.order_by("level", "name")
        else:
            self.fields["level"].choices = MANAGER_LEVELS
            self.fields["parent"].queryset = (
                accessible_locations(user)
                .filter(
                    is_active=True,
                    # FLOOR: parent for a new Storage Room (only reachable
                    # if granted at Floor level or above). STORAGE_ROOM:
                    # parent for a new Rack/Cabinet — accessible_locations()
                    # includes the granted node itself, so a Stock Manager
                    # granted at exactly Storage Room level (this app's own
                    # standard scenario — see tests.conftest.
                    # stock_manager_with_room_access) can still bootstrap
                    # fixtures inside their own room. RACK_CABINET: parent
                    # for a new Shelf/Bin.
                    level__in=(
                        Location.Level.FLOOR,
                        Location.Level.STORAGE_ROOM,
                        Location.Level.RACK_CABINET,
                    ),
                )
                .order_by("level", "name")
            )


class LocationEditForm(forms.Form):
    name = forms.CharField(max_length=120)
    code = forms.CharField(max_length=30, required=False)
