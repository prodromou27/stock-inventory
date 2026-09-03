import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q

from apps.core.authorization import is_administrator

from ..models import SavedGridView

# Hard cap on the JSON blob's size — this is UI presentation state (column
# widths/order/visibility, filter values, sort, density), not user content;
# anything larger is either abuse or a client bug, not a real saved view.
MAX_STATE_BYTES = 20_000


def list_saved_grid_views(*, user, grid_key):
    """Own views + anyone's shared views for this grid — the same
    "mine or shared, never someone else's private one" rule as
    apps.reporting.services' SavedReport listing.
    """
    return SavedGridView.objects.filter(grid_key=grid_key).filter(
        Q(created_by=user) | Q(is_shared=True)
    )


def create_saved_grid_view(*, user, name, grid_key, state, is_shared=False):
    name = name.strip()
    if not name:
        raise ValidationError("Name is required.")
    if grid_key not in dict(SavedGridView.GRID_CHOICES):
        raise ValidationError("Unknown grid.")
    if not isinstance(state, dict):
        raise ValidationError("Invalid view state.")
    if len(json.dumps(state)) > MAX_STATE_BYTES:
        raise ValidationError("Saved view is too large.")

    # Only an Administrator's saved views can be shared with everyone —
    # enforced here, not just hidden/disabled in the form.
    if is_shared and not is_administrator(user):
        is_shared = False

    view = SavedGridView(
        name=name,
        grid_key=grid_key,
        state=state,
        is_shared=is_shared,
        created_by=user,
        updated_by=user,
    )
    view.full_clean()
    view.save()
    return view


def delete_saved_grid_view(*, view, user):
    if view.created_by_id != user.id and not is_administrator(user):
        raise PermissionDenied("You can only delete your own saved views.")
    view.delete()
