import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

User = get_user_model()


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_seed_dev_data_creates_one_user_per_role():
    call_command("seed_dev_data")

    assert User.objects.filter(username="devadmin", groups__name="Administrator").exists()
    assert User.objects.filter(username="devmanager", groups__name="StockManager").exists()
    assert User.objects.filter(username="devreadonly", groups__name="ReadOnlyUser").exists()


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_seed_dev_data_refuses_to_run_outside_debug():
    with pytest.raises(CommandError):
        call_command("seed_dev_data")
