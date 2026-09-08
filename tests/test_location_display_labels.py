import pytest

from apps.inventory.forms import ReceiveBulkBatchForm
from apps.locations.models import Location
from apps.locations.services import create_location

pytestmark = pytest.mark.django_db


def test_receiving_location_labels_include_room_and_floor(
    administrator, stock_manager_with_room_access, location_tree, django_assert_num_queries
):
    floor = create_location(
        user=administrator,
        level=Location.Level.RACK_SHELF,
        name="B",
        parent=location_tree["room"],
    )
    form = ReceiveBulkBatchForm(user=stock_manager_with_room_access)
    field = form.fields["default_location"]
    # Ancestors are joined once, not fetched separately for each option.
    locations = list(field.queryset)
    with django_assert_num_queries(0):
        labels = {str(node.pk): field.label_from_instance(node) for node in locations}
    assert labels[str(floor.pk)] == "Wonderland > Room A > B (Shelf/Rack)"
    assert labels[str(location_tree["room"].pk)] == "Wonderland > Room A (Storage Room)"
    assert floor.level == "rack_shelf"


def test_location_label_change_does_not_expand_access(stock_manager):
    form = ReceiveBulkBatchForm(user=stock_manager)
    assert not form.fields["default_location"].queryset.exists()
