import os
import shutil
import tempfile

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from apps.locations.models import Location
from apps.locations.services import create_location

User = get_user_model()


@pytest.fixture
def certs_dir(settings):
    """tempfile.mkdtemp(), not pytest's tmp_path — this repo's CI/most local
    setups don't need the difference, but tmp_path's own bootstrap can fail
    with PermissionError on a Windows dev machine where a stale
    pytest-of-<user> temp dir was left in a locked state (unrelated to this
    app; a machine-local environment quirk). tempfile.mkdtemp() sidesteps
    pytest's own temp-dir machinery entirely. Points
    apps.settings.services.update_certificate's write target
    (settings.CERTS_DIR) at it for the duration of the test.
    """
    path = tempfile.mkdtemp(prefix="stock_inventory_certs_test_")
    settings.CERTS_DIR = path
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def unwritable_path():
    """A path guaranteed to make os.makedirs()/open() raise OSError on any
    OS: a regular file sits where a directory component needs to be, so
    walking into it as a directory fails. A hardcoded string like
    "Z:\\no\\such\\path" only fails on Windows — on Linux (e.g. GitHub
    Actions' runners) backslashes aren't path separators, so the whole
    string is just an unusual-but-valid relative path component, and
    os.makedirs() happily creates it (caught by CI, not by local testing on
    Windows alone).

    tempfile.mkdtemp(), not pytest's tmp_path — see certs_dir's docstring
    above for why.
    """
    base = tempfile.mkdtemp(prefix="stock_inventory_unwritable_test_")
    blocker = os.path.join(base, "blocker")
    with open(blocker, "wb") as f:
        f.write(b"not a directory")
    yield os.path.join(blocker, "subdir")
    shutil.rmtree(base, ignore_errors=True)


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


@pytest.fixture
def stock_manager_with_room_access(stock_manager, administrator, location_tree):
    from apps.accounts.services import grant_location_access

    grant_location_access(
        user=stock_manager, location=location_tree["room"], granted_by=administrator
    )
    return stock_manager


@pytest.fixture
def unit_product(administrator):
    from apps.catalog.models import ItemCategory
    from apps.catalog.services import create_product

    return create_product(
        user=administrator,
        brand_name="Fortinet",
        model="FG-100F",
        product_type_name="Firewall",
        category=ItemCategory.SERIALIZED_ASSET,
    )


@pytest.fixture
def quantity_product(administrator):
    from apps.catalog.models import ItemCategory
    from apps.catalog.services import create_product

    return create_product(
        user=administrator,
        brand_name="HP",
        model="26A",
        product_type_name="Toner",
        category=ItemCategory.QUANTITY_STOCK,
        low_stock_threshold=5,
    )
