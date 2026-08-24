import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from apps.locations.models import Location
from apps.locations.services import create_location

User = get_user_model()


@pytest.fixture
def administrator(db):
    user = User.objects.create_user(username="admin1", password="a-strong-test-password-123")
    user.groups.add(Group.objects.get(name="Administrator"))
    return user


@pytest.fixture
def stock_manager(db):
    user = User.objects.create_user(username="manager1", password="a-strong-test-password-123")
    user.groups.add(Group.objects.get(name="StockManager"))
    return user


@pytest.fixture
def read_only_user(db):
    user = User.objects.create_user(username="reader1", password="a-strong-test-password-123")
    user.groups.add(Group.objects.get(name="ReadOnlyUser"))
    return user


@pytest.fixture
def location_tree(administrator):
    country = create_location(level=Location.Level.COUNTRY, name="Wonderland", user=administrator)
    site = create_location(level=Location.Level.SITE, name="HQ", parent=country, user=administrator)
    floor = create_location(
        level=Location.Level.FLOOR, name="1st Floor", parent=site, user=administrator
    )
    room = create_location(
        level=Location.Level.STORAGE_ROOM, name="Room A", parent=floor, user=administrator
    )
    return {"country": country, "site": site, "floor": floor, "room": room}


@pytest.fixture
def other_location_tree(administrator):
    country = create_location(level=Location.Level.COUNTRY, name="Elsewhere", user=administrator)
    site = create_location(
        level=Location.Level.SITE, name="Other HQ", parent=country, user=administrator
    )
    return {"country": country, "site": site}
