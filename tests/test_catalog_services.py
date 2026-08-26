import pytest
from django.core.exceptions import ValidationError

from apps.audit.models import AuditEvent
from apps.catalog.models import Brand, Product, ProductType, TrackingMethod
from apps.catalog.services import (
    DuplicateProductError,
    check_duplicate_products,
    create_product,
    create_products_batch,
    get_or_create_brand,
    get_or_create_product_type,
    update_product,
)


@pytest.mark.django_db
class TestBrandAndProductTypeLookups:
    def test_get_or_create_brand_reuses_existing_case_insensitively(self, administrator):
        first = get_or_create_brand("Fortinet", user=administrator)
        second = get_or_create_brand("  FORTINET  ", user=administrator)

        assert first.pk == second.pk
        assert Brand.objects.count() == 1

    def test_get_or_create_product_type_reuses_existing(self, administrator):
        first = get_or_create_product_type("Firewall", user=administrator)
        second = get_or_create_product_type("firewall", user=administrator)

        assert first.pk == second.pk
        assert ProductType.objects.count() == 1

    def test_brand_creation_is_audited(self, administrator):
        get_or_create_brand("NewBrand", user=administrator)

        assert AuditEvent.objects.filter(
            object_type="Brand", event_type=AuditEvent.EventType.RECORD_CREATED
        ).exists()


@pytest.mark.django_db
class TestCreateProduct:
    def test_create_unit_product(self, administrator):
        product = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            product_type_name="Firewall",
            tracking_method=TrackingMethod.UNIT,
        )

        assert product.tracking_method == TrackingMethod.UNIT
        assert product.low_stock_threshold is None

    def test_create_quantity_product_with_threshold(self, administrator):
        product = create_product(
            user=administrator,
            brand_name="HP",
            model="26A",
            product_type_name="Toner",
            tracking_method=TrackingMethod.QUANTITY,
            low_stock_threshold=5,
        )

        assert product.low_stock_threshold == 5

    def test_low_stock_threshold_ignored_for_unit_products(self, administrator):
        product = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-101F",
            product_type_name="Firewall",
            tracking_method=TrackingMethod.UNIT,
            low_stock_threshold=10,
        )

        assert product.low_stock_threshold is None

    def test_duplicate_brand_model_blocked_without_acknowledgement(self, administrator):
        create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            product_type_name="Firewall",
            tracking_method=TrackingMethod.UNIT,
        )

        with pytest.raises(DuplicateProductError) as exc_info:
            create_product(
                user=administrator,
                brand_name="fortinet",
                model="fg-100f",
                product_type_name="Firewall",
                tracking_method=TrackingMethod.UNIT,
            )
        assert len(exc_info.value.matches) == 1

    def test_duplicate_allowed_with_acknowledgement_and_is_audited(self, administrator):
        create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            product_type_name="Firewall",
            tracking_method=TrackingMethod.UNIT,
        )

        second = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            product_type_name="Firewall",
            tracking_method=TrackingMethod.UNIT,
            duplicate_acknowledged=True,
        )

        assert Product.objects.filter(model__iexact="fg-100f").count() == 2
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.DUPLICATE_PRODUCT_ACKNOWLEDGED,
            object_id=str(second.pk),
        ).exists()

    def test_different_sku_same_brand_model_still_flagged(self, administrator):
        create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            sku="SKU-A",
            product_type_name="Firewall",
            tracking_method=TrackingMethod.UNIT,
        )

        with pytest.raises(DuplicateProductError):
            create_product(
                user=administrator,
                brand_name="Fortinet",
                model="FG-100F",
                sku="SKU-B",
                product_type_name="Firewall",
                tracking_method=TrackingMethod.UNIT,
            )

    def test_read_only_user_cannot_create_product(self, read_only_user):
        with pytest.raises(Exception):
            create_product(
                user=read_only_user,
                brand_name="Fortinet",
                model="FG-100F",
                product_type_name="Firewall",
                tracking_method=TrackingMethod.UNIT,
            )

    def test_create_product_is_audited(self, administrator):
        product = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-102F",
            product_type_name="Firewall",
            tracking_method=TrackingMethod.UNIT,
        )

        assert AuditEvent.objects.filter(
            object_type="Product",
            object_id=str(product.pk),
            event_type=AuditEvent.EventType.RECORD_CREATED,
        ).exists()


@pytest.mark.django_db
class TestCreateProductsBatch:
    """apps.catalog.views.QuickAddProductsView's service — several rows,
    each its own create_product() call.
    """

    def test_creates_one_product_per_row(self, administrator):
        results = create_products_batch(
            user=administrator,
            rows=[
                {
                    "brand_name": "Fortinet",
                    "model": "FG-100F",
                    "sku": "",
                    "product_type_name": "Firewall",
                    "tracking_method": TrackingMethod.UNIT,
                    "supplier": "",
                },
                {
                    "brand_name": "HP",
                    "model": "26A",
                    "sku": "",
                    "product_type_name": "Toner",
                    "tracking_method": TrackingMethod.QUANTITY,
                    "supplier": "",
                },
            ],
        )
        assert [r["status"] for r in results] == ["created", "created"]
        assert Product.objects.filter(model="FG-100F").exists()
        assert Product.objects.filter(model="26A").exists()

    def test_duplicate_row_is_reported_not_raised(self, administrator):
        product = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            product_type_name="Firewall",
            tracking_method=TrackingMethod.UNIT,
        )

        results = create_products_batch(
            user=administrator,
            rows=[
                {
                    "brand_name": product.brand.name,
                    "model": product.model,
                    "sku": "",
                    "product_type_name": product.product_type.name,
                    "tracking_method": TrackingMethod.UNIT,
                    "supplier": "",
                }
            ],
        )
        assert results[0]["status"] == "duplicate"
        assert results[0]["matches"] == [product]
        assert Product.objects.filter(model="FG-100F").count() == 1

    def test_one_bad_row_does_not_block_the_rest_of_the_batch(self, administrator):
        product = create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            product_type_name="Firewall",
            tracking_method=TrackingMethod.UNIT,
        )

        results = create_products_batch(
            user=administrator,
            rows=[
                {
                    "brand_name": "Cisco",
                    "model": "RV340",
                    "sku": "",
                    "product_type_name": "Router",
                    "tracking_method": TrackingMethod.UNIT,
                    "supplier": "",
                },
                {
                    "brand_name": product.brand.name,
                    "model": product.model,
                    "sku": "",
                    "product_type_name": product.product_type.name,
                    "tracking_method": TrackingMethod.UNIT,
                    "supplier": "",
                },
                {
                    "brand_name": "Cisco",
                    "model": "RV345",
                    "sku": "",
                    "product_type_name": "Router",
                    "tracking_method": TrackingMethod.UNIT,
                    "supplier": "",
                },
            ],
        )
        assert [r["status"] for r in results] == ["created", "duplicate", "created"]
        assert Product.objects.filter(model="RV340").exists()
        assert Product.objects.filter(model="RV345").exists()

    def test_read_only_user_cannot_batch_create(self, read_only_user):
        with pytest.raises(Exception):
            create_products_batch(
                user=read_only_user,
                rows=[
                    {
                        "brand_name": "Fortinet",
                        "model": "FG-100F",
                        "sku": "",
                        "product_type_name": "Firewall",
                        "tracking_method": TrackingMethod.UNIT,
                        "supplier": "",
                    }
                ],
            )


@pytest.mark.django_db
class TestCheckDuplicateProducts:
    def test_no_match_for_distinct_products(self, administrator):
        brand = get_or_create_brand("Fortinet", user=administrator)
        create_product(
            user=administrator,
            brand_name="Fortinet",
            model="FG-100F",
            product_type_name="Firewall",
            tracking_method=TrackingMethod.UNIT,
        )

        matches = check_duplicate_products(brand=brand, model="FG-200F")
        assert matches.count() == 0


@pytest.mark.django_db
class TestUpdateProductTrackingMethodLock:
    def test_tracking_method_changeable_before_any_movement(self, administrator, unit_product):
        updated = update_product(
            product=unit_product,
            user=administrator,
            brand_name=unit_product.brand.name,
            model=unit_product.model,
            product_type_name=unit_product.product_type.name,
            tracking_method=TrackingMethod.QUANTITY,
        )
        assert updated.tracking_method == TrackingMethod.QUANTITY

    def test_tracking_method_locked_after_movement(
        self, administrator, unit_product, location_tree
    ):
        from datetime import date

        from apps.inventory.services.receipts import receive_stock

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-LOCK",
        )

        with pytest.raises(ValidationError):
            update_product(
                product=unit_product,
                user=administrator,
                brand_name=unit_product.brand.name,
                model=unit_product.model,
                product_type_name=unit_product.product_type.name,
                tracking_method=TrackingMethod.QUANTITY,
            )

    def test_non_tracking_fields_still_editable_after_movement(
        self, administrator, unit_product, location_tree
    ):
        from datetime import date

        from apps.inventory.services.receipts import receive_stock

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-LOCK-2",
        )

        updated = update_product(
            product=unit_product,
            user=administrator,
            brand_name=unit_product.brand.name,
            model=unit_product.model,
            product_type_name=unit_product.product_type.name,
            tracking_method=unit_product.tracking_method,
            description="Updated description",
        )
        assert updated.description == "Updated description"

    def test_update_is_audited(self, administrator, unit_product):
        update_product(
            product=unit_product,
            user=administrator,
            brand_name=unit_product.brand.name,
            model=unit_product.model,
            product_type_name=unit_product.product_type.name,
            tracking_method=unit_product.tracking_method,
            description="New description",
        )

        assert AuditEvent.objects.filter(
            object_id=str(unit_product.pk), event_type=AuditEvent.EventType.RECORD_UPDATED
        ).exists()
