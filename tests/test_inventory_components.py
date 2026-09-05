from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.catalog.models import ItemCategory
from apps.catalog.services import create_product
from apps.inventory.models import MovementType, UnitAsset, UnitStatus
from apps.inventory.services.components import install_component, remove_component
from apps.inventory.services.receipts import receive_stock


@pytest.fixture
def component_product(administrator):
    return create_product(
        user=administrator,
        brand_name="Crucial",
        model="RAM-16GB",
        product_type_name="Memory Module",
        category=ItemCategory.COMPONENT,
    )


@pytest.fixture
def parent_product(administrator):
    return create_product(
        user=administrator,
        brand_name="Dell",
        model="Latitude-5420",
        product_type_name="Laptop",
        category=ItemCategory.SERIALIZED_ASSET,
    )


@pytest.fixture
def component_asset(administrator, component_product, location_tree):
    receive_stock(
        user=administrator,
        product=component_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="COMP-1",
    )
    return UnitAsset.objects.get(vendor_serial="COMP-1")


@pytest.fixture
def parent_asset(administrator, parent_product, location_tree):
    receive_stock(
        user=administrator,
        product=parent_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="LAPTOP-1",
    )
    return UnitAsset.objects.get(vendor_serial="LAPTOP-1")


@pytest.mark.django_db
class TestInstallComponentService:
    def test_installs_and_writes_ledger(
        self, administrator, component_asset, parent_asset, location_tree
    ):
        txn = install_component(
            user=administrator,
            component_id=component_asset.pk,
            parent_id=parent_asset.pk,
            occurred_at=date.today(),
        )
        component_asset.refresh_from_db()
        assert component_asset.installed_in_id == parent_asset.pk
        assert component_asset.status == UnitStatus.IN_STOCK
        assert txn.movement_type == MovementType.INSTALL_COMPONENT
        assert txn.lines.count() == 1
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.COMPONENT_ASSOCIATION_CHANGED,
            object_id=str(component_asset.pk),
        ).exists()

    def test_rejects_non_component_category(self, administrator, parent_asset, location_tree):
        other = create_product(
            user=administrator,
            brand_name="Dell",
            model="Latitude-9999",
            product_type_name="Laptop",
            category=ItemCategory.SERIALIZED_ASSET,
        )
        receive_stock(
            user=administrator,
            product=other,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="NOT-A-COMPONENT",
        )
        not_component = UnitAsset.objects.get(vendor_serial="NOT-A-COMPONENT")

        with pytest.raises(ValidationError, match="Only Component-category"):
            install_component(
                user=administrator,
                component_id=not_component.pk,
                parent_id=parent_asset.pk,
                occurred_at=date.today(),
            )

    def test_rejects_component_already_installed(
        self, administrator, component_asset, parent_asset, location_tree, parent_product
    ):
        install_component(
            user=administrator,
            component_id=component_asset.pk,
            parent_id=parent_asset.pk,
            occurred_at=date.today(),
        )
        receive_stock(
            user=administrator,
            product=parent_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="LAPTOP-2",
        )
        other_parent = UnitAsset.objects.get(vendor_serial="LAPTOP-2")

        with pytest.raises(ValidationError, match="already installed"):
            install_component(
                user=administrator,
                component_id=component_asset.pk,
                parent_id=other_parent.pk,
                occurred_at=date.today(),
            )

    def test_rejects_when_component_not_in_stock(
        self, administrator, component_asset, parent_asset
    ):
        component_asset.status = UnitStatus.DAMAGED
        component_asset.save(update_fields=["status"])

        with pytest.raises(ValidationError, match="must be In Stock"):
            install_component(
                user=administrator,
                component_id=component_asset.pk,
                parent_id=parent_asset.pk,
                occurred_at=date.today(),
            )

    def test_rejects_self_install(self, administrator, component_asset):
        with pytest.raises(ValidationError, match="cannot be installed into itself"):
            install_component(
                user=administrator,
                component_id=component_asset.pk,
                parent_id=component_asset.pk,
                occurred_at=date.today(),
            )

    def test_rejects_cycle(self, administrator, component_product, location_tree):
        """A Component-category item can itself host another component (a
        connector board with a sub-module, say). Nesting one level is fine;
        closing the loop back on itself must be rejected.
        """
        receive_stock(
            user=administrator,
            product=component_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="COMP-A",
        )
        receive_stock(
            user=administrator,
            product=component_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="COMP-B",
        )
        comp_a = UnitAsset.objects.get(vendor_serial="COMP-A")
        comp_b = UnitAsset.objects.get(vendor_serial="COMP-B")

        # comp_a installed into comp_b — one level of nesting, allowed.
        install_component(
            user=administrator,
            component_id=comp_a.pk,
            parent_id=comp_b.pk,
            occurred_at=date.today(),
        )
        comp_a.refresh_from_db()
        assert comp_a.installed_in_id == comp_b.pk

        # Installing comp_b into comp_a now would close the loop.
        with pytest.raises(ValidationError, match="installed inside itself"):
            install_component(
                user=administrator,
                component_id=comp_b.pk,
                parent_id=comp_a.pk,
                occurred_at=date.today(),
            )

    def test_enforces_location_access(
        self,
        administrator,
        stock_manager_with_room_access,
        component_asset,
        parent_asset,
        other_location_tree,
    ):
        from apps.locations.models import Location
        from apps.locations.services import create_location

        other_floor = create_location(
            level=Location.Level.FLOOR,
            name="Out of scope floor",
            parent=other_location_tree["site"],
            user=administrator,
        )
        other_room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="Out of scope room",
            parent=other_floor,
            user=administrator,
        )
        parent_asset.current_location = other_room
        parent_asset.save(update_fields=["current_location"])

        with pytest.raises(PermissionDenied):
            install_component(
                user=stock_manager_with_room_access,
                component_id=component_asset.pk,
                parent_id=parent_asset.pk,
                occurred_at=date.today(),
            )


@pytest.mark.django_db
class TestRemoveComponentService:
    def test_removes_and_writes_ledger(self, administrator, component_asset, parent_asset):
        install_component(
            user=administrator,
            component_id=component_asset.pk,
            parent_id=parent_asset.pk,
            occurred_at=date.today(),
        )
        txn = remove_component(
            user=administrator, component_id=component_asset.pk, occurred_at=date.today()
        )
        component_asset.refresh_from_db()
        assert component_asset.installed_in_id is None
        assert component_asset.status == UnitStatus.IN_STOCK
        assert txn.movement_type == MovementType.REMOVE_COMPONENT
        assert (
            AuditEvent.objects.filter(
                event_type=AuditEvent.EventType.COMPONENT_ASSOCIATION_CHANGED,
                object_id=str(component_asset.pk),
            ).count()
            == 2
        )  # one for install, one for remove

    def test_rejects_when_not_installed(self, administrator, component_asset):
        with pytest.raises(ValidationError, match="isn't currently installed"):
            remove_component(
                user=administrator, component_id=component_asset.pk, occurred_at=date.today()
            )


@pytest.mark.django_db
class TestInstallComponentView:
    def test_read_only_user_forbidden(self, client, read_only_user, component_asset):
        client.force_login(read_only_user)
        response = client.get(
            reverse("inventory:install_component", kwargs={"pk": component_asset.pk})
        )
        assert response.status_code == 403

    def test_parent_choices_exclude_the_component_itself(
        self, client, stock_manager_with_room_access, component_asset, parent_asset
    ):
        client.force_login(stock_manager_with_room_access)
        response = client.get(
            reverse("inventory:install_component", kwargs={"pk": component_asset.pk})
        )
        choices = list(response.context["form"].fields["parent_asset"].queryset)
        assert parent_asset in choices
        assert component_asset not in choices

    def test_post_installs_and_redirects(
        self, client, stock_manager_with_room_access, component_asset, parent_asset
    ):
        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:install_component", kwargs={"pk": component_asset.pk}),
            {"parent_asset": parent_asset.pk, "occurred_at": date.today().isoformat()},
        )
        assert response.status_code == 302
        component_asset.refresh_from_db()
        assert component_asset.installed_in_id == parent_asset.pk

    def test_invalid_submission_reshows_form_with_error(
        self, client, stock_manager_with_room_access, component_asset, parent_asset
    ):
        component_asset.status = UnitStatus.DAMAGED
        component_asset.save(update_fields=["status"])
        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:install_component", kwargs={"pk": component_asset.pk}),
            {"parent_asset": parent_asset.pk, "occurred_at": date.today().isoformat()},
        )
        assert response.status_code == 200
        assert "must be In Stock" in str(response.context["form"].errors)


@pytest.mark.django_db
class TestRemoveComponentView:
    def test_post_removes_and_redirects(
        self, client, stock_manager_with_room_access, component_asset, parent_asset
    ):
        install_component(
            user=stock_manager_with_room_access,
            component_id=component_asset.pk,
            parent_id=parent_asset.pk,
            occurred_at=date.today(),
        )
        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("inventory:remove_component", kwargs={"pk": component_asset.pk}),
            {"occurred_at": date.today().isoformat()},
        )
        assert response.status_code == 302
        component_asset.refresh_from_db()
        assert component_asset.installed_in_id is None


@pytest.mark.django_db
class TestAssetDetailComponentSection:
    def test_shows_install_link_for_eligible_component(
        self, client, stock_manager_with_room_access, component_asset
    ):
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:asset_detail", kwargs={"pk": component_asset.pk}))
        assert response.status_code == 200
        assert response.context["can_install_component"] is True

    def test_shows_installed_components_on_parent_page(
        self, client, stock_manager_with_room_access, component_asset, parent_asset
    ):
        install_component(
            user=stock_manager_with_room_access,
            component_id=component_asset.pk,
            parent_id=parent_asset.pk,
            occurred_at=date.today(),
        )
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("inventory:asset_detail", kwargs={"pk": parent_asset.pk}))
        installed = list(response.context["installed_components"])
        assert installed == [component_asset]
