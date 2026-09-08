from django import forms

from .models import ROOM_OR_BELOW_LEVELS, Location, order_by_hierarchy
from .scoping import accessible_locations

MANAGER_LEVELS = tuple((level, level.label) for level in ROOM_OR_BELOW_LEVELS)


def location_choice_label(obj):
    breadcrumb = " > ".join(loc.name for loc in [*obj.ancestors(), obj])
    return f"{breadcrumb} ({obj.get_level_display()})"


class LocationChoiceField(forms.ModelChoiceField):
    """Labels each option with its level and full breadcrumb (e.g. "Wonderland
    > Room A (Storage Room)") — a flat dropdown over locations at more than
    one level is otherwise ambiguous between two same-named rooms under
    different countries. A per-row .ancestors() walk is fine here (unlike a
    grid — see _build_location_tree()'s docstring for why that distinction
    matters): every field that uses this is a low-traffic admin/setup
    screen, not a hot path iterated at grid scale.
    """

    def label_from_instance(self, obj):
        return location_choice_label(obj)


class LocationForm(forms.Form):
    """A plain Form, not a ModelForm — creation goes through
    services.create_location() so validation/audit logic lives in one place.
    """

    level = forms.ChoiceField(choices=Location.Level.choices)
    name = forms.CharField(max_length=120)
    code = forms.CharField(max_length=30, required=False)
    parent = LocationChoiceField(
        queryset=Location.objects.none(),
        required=False,
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.core.authorization import is_administrator

        parents = Location.objects.filter(is_active=True)
        if is_administrator(user):
            self.fields["parent"].queryset = order_by_hierarchy(parents)
        else:
            self.fields["level"].choices = MANAGER_LEVELS
            self.fields["parent"].queryset = order_by_hierarchy(
                accessible_locations(user).filter(
                    is_active=True,
                    # COUNTRY: parent for a new Storage Room (only reachable
                    # if granted at Country level or above). STORAGE_ROOM:
                    # parent for a new Rack/Shelf — accessible_locations()
                    # includes the granted node itself, so a Stock Manager
                    # granted at exactly Storage Room level (this app's own
                    # standard scenario — see tests.conftest.
                    # stock_manager_with_room_access) can still bootstrap
                    # fixtures inside their own room.
                    level__in=(Location.Level.COUNTRY, Location.Level.STORAGE_ROOM),
                )
            )


class LocationEditForm(forms.Form):
    name = forms.CharField(max_length=120)
    code = forms.CharField(max_length=30, required=False)
