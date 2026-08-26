import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.catalog.forms import ProductForm, custom_field_key
from apps.catalog.models import ProductCustomFieldDefinition, ProductCustomFieldType
from apps.catalog.services import (
    create_custom_field_definition,
    create_product,
    set_custom_field_definition_active,
    update_product,
)


@pytest.fixture
def warranty_field(administrator):
    return create_custom_field_definition(
        user=administrator, name="Warranty expiry", field_type=ProductCustomFieldType.DATE
    )


@pytest.fixture
def notes_field(administrator):
    return create_custom_field_definition(
        user=administrator, name="Internal notes", field_type=ProductCustomFieldType.TEXT
    )


@pytest.mark.django_db
class TestCreateCustomFieldDefinition:
    def test_administrator_can_create(self, administrator):
        definition = create_custom_field_definition(
            user=administrator, name="Warranty expiry", field_type=ProductCustomFieldType.DATE
        )
        assert definition.is_active is True
        assert ProductCustomFieldDefinition.objects.count() == 1

    def test_stock_manager_cannot_create(self, stock_manager):
        with pytest.raises(PermissionDenied):
            create_custom_field_definition(
                user=stock_manager, name="Warranty expiry", field_type=ProductCustomFieldType.DATE
            )

    def test_blank_name_rejected(self, administrator):
        with pytest.raises(ValidationError):
            create_custom_field_definition(
                user=administrator, name="   ", field_type=ProductCustomFieldType.TEXT
            )

    def test_unknown_field_type_rejected(self, administrator):
        with pytest.raises(ValidationError):
            create_custom_field_definition(
                user=administrator, name="Whatever", field_type="not-a-real-type"
            )


@pytest.mark.django_db
class TestSetCustomFieldDefinitionActive:
    def test_administrator_can_deactivate_and_reactivate(self, administrator, warranty_field):
        set_custom_field_definition_active(
            definition=warranty_field, user=administrator, is_active=False
        )
        warranty_field.refresh_from_db()
        assert warranty_field.is_active is False

        set_custom_field_definition_active(
            definition=warranty_field, user=administrator, is_active=True
        )
        warranty_field.refresh_from_db()
        assert warranty_field.is_active is True

    def test_stock_manager_cannot_toggle(self, stock_manager, warranty_field):
        with pytest.raises(PermissionDenied):
            set_custom_field_definition_active(
                definition=warranty_field, user=stock_manager, is_active=False
            )


@pytest.mark.django_db
class TestProductFormDynamicFields:
    def test_active_definitions_appear_as_form_fields(self, warranty_field, notes_field):
        form = ProductForm()
        assert custom_field_key(warranty_field.pk) in form.fields
        assert custom_field_key(notes_field.pk) in form.fields
        assert form.fields[custom_field_key(warranty_field.pk)].label == "Warranty expiry"

    def test_inactive_definitions_do_not_appear(self, administrator, warranty_field):
        set_custom_field_definition_active(
            definition=warranty_field, user=administrator, is_active=False
        )
        form = ProductForm()
        assert custom_field_key(warranty_field.pk) not in form.fields


@pytest.mark.django_db
class TestCustomFieldValuesOnProduct:
    def test_value_round_trips_through_create_and_update(self, administrator, notes_field):
        product = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            product_type_name="Firewall",
            tracking_method="unit",
            custom_field_values={str(notes_field.pk): "Handle with care"},
        )
        assert product.custom_field_values == {str(notes_field.pk): "Handle with care"}

        updated = update_product(
            product=product,
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            product_type_name="Firewall",
            tracking_method="unit",
            custom_field_values={str(notes_field.pk): "Updated note"},
        )
        assert updated.custom_field_values == {str(notes_field.pk): "Updated note"}

    def test_date_value_is_stored_as_iso_string(self, administrator, warranty_field):
        import datetime

        product = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            product_type_name="Firewall",
            tracking_method="unit",
            custom_field_values={str(warranty_field.pk): datetime.date(2027, 6, 1)},
        )
        assert product.custom_field_values == {str(warranty_field.pk): "2027-06-01"}

    def test_unknown_key_is_silently_dropped_not_raised(self, administrator):
        product = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            product_type_name="Firewall",
            tracking_method="unit",
            custom_field_values={"not-a-real-definition-id": "whatever"},
        )
        assert product.custom_field_values == {}

    def test_blank_value_is_not_stored(self, administrator, notes_field):
        product = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            product_type_name="Firewall",
            tracking_method="unit",
            custom_field_values={str(notes_field.pk): ""},
        )
        assert product.custom_field_values == {}

    def test_deactivated_definitions_value_survives_an_unrelated_update(
        self, administrator, notes_field
    ):
        product = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            product_type_name="Firewall",
            tracking_method="unit",
            custom_field_values={str(notes_field.pk): "Keep me"},
        )
        set_custom_field_definition_active(
            definition=notes_field, user=administrator, is_active=False
        )

        updated = update_product(
            product=product,
            user=administrator,
            brand_name="Fortinet",
            model="FG-100G",
            product_type_name="Firewall",
            tracking_method="unit",
            custom_field_values={},
        )
        assert updated.custom_field_values == {str(notes_field.pk): "Keep me"}


@pytest.mark.django_db
class TestCustomFieldDefinitionViews:
    def test_read_only_user_cannot_view_list(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("catalog:custom_field_list"))
        assert response.status_code == 403

    def test_stock_manager_cannot_view_list(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.get(reverse("catalog:custom_field_list"))
        assert response.status_code == 403

    def test_administrator_can_create_via_view(self, client, administrator):
        client.force_login(administrator)
        response = client.post(
            reverse("catalog:custom_field_create"),
            {"name": "Warranty expiry", "field_type": "date", "display_order": 0},
        )
        assert response.status_code == 302
        assert ProductCustomFieldDefinition.objects.filter(name="Warranty expiry").exists()

    def test_administrator_can_toggle_active_via_view(self, client, administrator, warranty_field):
        client.force_login(administrator)
        response = client.post(
            reverse("catalog:custom_field_toggle_active", args=[warranty_field.pk])
        )
        assert response.status_code == 302
        warranty_field.refresh_from_db()
        assert warranty_field.is_active is False

    def test_product_create_view_saves_custom_field_value(self, client, stock_manager, notes_field):
        client.force_login(stock_manager)
        response = client.post(
            reverse("catalog:product_create"),
            {
                "brand_name": "Fortinet",
                "model": "FG-100F",
                "product_type_name": "Firewall",
                "tracking_method": "unit",
                custom_field_key(notes_field.pk): "Fragile",
            },
        )
        assert response.status_code == 302
        from apps.catalog.models import Product

        product = Product.objects.get(model="FG-100F")
        assert product.custom_field_values == {str(notes_field.pk): "Fragile"}

    def test_product_update_view_shows_existing_value_as_initial(
        self, client, administrator, unit_product, notes_field
    ):
        update_product(
            product=unit_product,
            user=administrator,
            brand_name=unit_product.brand.name,
            model=unit_product.model,
            product_type_name=unit_product.product_type.name,
            tracking_method=unit_product.tracking_method,
            custom_field_values={str(notes_field.pk): "Existing value"},
        )
        client.force_login(administrator)
        response = client.get(reverse("catalog:product_update", kwargs={"pk": unit_product.pk}))
        assert "Existing value" in response.content.decode()
