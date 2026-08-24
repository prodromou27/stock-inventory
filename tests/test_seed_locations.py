import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.locations.models import Location


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_seed_locations_requires_an_administrator_to_exist():
    with pytest.raises(CommandError):
        call_command("seed_locations")


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_seed_locations_creates_sample_tree(administrator):
    call_command("seed_locations")

    assert Location.objects.filter(level=Location.Level.COUNTRY, name="Greece").exists()
    assert Location.objects.filter(level=Location.Level.SHELF_BIN, name="Shelf 1").exists()


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_seed_locations_is_safe_to_run_twice(administrator):
    call_command("seed_locations")
    call_command("seed_locations")

    assert Location.objects.filter(level=Location.Level.COUNTRY, name="Greece").count() == 1


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_seed_locations_refuses_to_run_outside_debug():
    with pytest.raises(CommandError):
        call_command("seed_locations")
