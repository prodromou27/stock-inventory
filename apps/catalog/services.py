from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role
from apps.core.text import normalize_whitespace

from .models import Brand, Product, ProductType, TrackingMethod


class DuplicateProductError(Exception):
    """Raised when a create_product() call finds Brand/Model/SKU matches and
    the caller hasn't set duplicate_acknowledged=True. Carries the matches so
    the view can show them (docs/architecture/05-tracking-and-duplicates.md).
    """

    def __init__(self, matches):
        self.matches = list(matches)
        super().__init__("A product with a matching Brand/Model/SKU already exists.")


def get_or_create_brand(name, *, user):
    name = normalize_whitespace(name)
    if not name:
        raise ValidationError("Brand is required.")

    existing = Brand.objects.filter(name__iexact=name).first()
    if existing:
        return existing

    brand = Brand(name=name)
    brand.full_clean()
    brand.save()
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_CREATED,
        obj=brand,
        summary=f"Created brand '{brand.name}'",
        new_values={"name": brand.name},
    )
    return brand


def get_or_create_product_type(name, *, user):
    name = normalize_whitespace(name)
    if not name:
        raise ValidationError("Type/category is required.")

    existing = ProductType.objects.filter(name__iexact=name).first()
    if existing:
        return existing

    product_type = ProductType(name=name)
    product_type.full_clean()
    product_type.save()
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_CREATED,
        obj=product_type,
        summary=f"Created product type '{product_type.name}'",
        new_values={"name": product_type.name},
    )
    return product_type


def check_duplicate_products(*, brand, model, sku=""):
    normalized_model = normalize_whitespace(model).lower()
    query = Q(brand=brand, normalized_model=normalized_model)

    normalized_sku = normalize_whitespace(sku).lower()
    if normalized_sku:
        query |= Q(brand=brand, normalized_sku=normalized_sku)

    return Product.objects.filter(query).select_related("brand", "product_type")


@transaction.atomic
def create_product(
    *,
    user,
    brand_name,
    model,
    product_type_name,
    tracking_method,
    sku="",
    description="",
    supplier="",
    default_notes="",
    low_stock_threshold=None,
    duplicate_acknowledged=False,
):
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)

    brand = get_or_create_brand(brand_name, user=user)
    product_type = get_or_create_product_type(product_type_name, user=user)

    duplicates = check_duplicate_products(brand=brand, model=model, sku=sku)
    if duplicates.exists() and not duplicate_acknowledged:
        raise DuplicateProductError(duplicates)

    if tracking_method != TrackingMethod.QUANTITY:
        low_stock_threshold = None

    product = Product(
        brand=brand,
        model=model,
        sku=sku,
        product_type=product_type,
        tracking_method=tracking_method,
        description=description,
        supplier=supplier,
        default_notes=default_notes,
        low_stock_threshold=low_stock_threshold,
        created_by=user,
        updated_by=user,
    )
    product.full_clean(exclude=["normalized_model", "normalized_sku"])
    product.save()

    if duplicates.exists():
        record_event(
            actor=user,
            event_type=AuditEvent.EventType.DUPLICATE_PRODUCT_ACKNOWLEDGED,
            obj=product,
            summary=f"Acknowledged duplicate Brand/Model/SKU when creating product '{product}'",
            metadata={"matched_product_ids": [str(p.pk) for p in duplicates]},
        )

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_CREATED,
        obj=product,
        summary=f"Created product '{product}'",
        new_values={
            "brand": brand.name,
            "model": product.model,
            "sku": product.sku,
            "tracking_method": product.tracking_method,
        },
    )
    return product


def create_products_batch(*, user, rows):
    """Quick Add (apps.catalog.views.QuickAddProductsView) — several products
    entered as one submission instead of one page load each. Calls
    create_product() once per row rather than a single bulk insert, so each
    row gets its own duplicate check/audit event exactly like creating it
    individually would, and one bad row (a Brand/Model/SKU match, a bad
    tracking method) doesn't cost the rest of the batch — returns a per-row
    outcome list instead of raising. Never auto-acknowledges a duplicate;
    a duplicate row is reported back for the operator to resolve
    individually (docs/architecture/05-tracking-and-duplicates.md's
    reasoning for the single-product flow applies here too).

    `rows` is a list of dicts with the same keys create_product() accepts
    (brand_name/model/product_type_name/tracking_method required; sku/
    supplier optional) — apps.catalog.forms.QuickAddProductRowForm's
    cleaned_data shape.
    """
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)

    results = []
    for row in rows:
        try:
            product = create_product(user=user, **row)
        except DuplicateProductError as exc:
            results.append(
                {
                    "brand_name": row["brand_name"],
                    "model": row["model"],
                    "status": "duplicate",
                    "matches": list(exc.matches),
                }
            )
        except ValidationError as exc:
            results.append(
                {
                    "brand_name": row["brand_name"],
                    "model": row["model"],
                    "status": "error",
                    "detail": "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc),
                }
            )
        else:
            results.append(
                {
                    "brand_name": row["brand_name"],
                    "model": row["model"],
                    "status": "created",
                    "product": product,
                }
            )
    return results


@transaction.atomic
def update_product(
    *,
    product,
    user,
    brand_name,
    model,
    product_type_name,
    tracking_method,
    sku="",
    description="",
    supplier="",
    default_notes="",
    low_stock_threshold=None,
    is_active=True,
):
    require_role(user, ADMINISTRATOR, STOCK_MANAGER)

    if tracking_method != product.tracking_method and product.has_movements():
        raise ValidationError(
            "Tracking method cannot be changed once movements exist for this product. "
            "This requires an Administrator migration operation, which is not yet implemented."
        )

    old_values = {
        "brand": product.brand.name,
        "model": product.model,
        "sku": product.sku,
        "tracking_method": product.tracking_method,
        "is_active": product.is_active,
    }

    if tracking_method != TrackingMethod.QUANTITY:
        low_stock_threshold = None

    product.brand = get_or_create_brand(brand_name, user=user)
    product.product_type = get_or_create_product_type(product_type_name, user=user)
    product.model = model
    product.sku = sku
    product.tracking_method = tracking_method
    product.description = description
    product.supplier = supplier
    product.default_notes = default_notes
    product.low_stock_threshold = low_stock_threshold
    product.is_active = is_active
    product.updated_by = user
    product.full_clean(exclude=["normalized_model", "normalized_sku"])
    product.save()

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=product,
        summary=f"Updated product '{product}'",
        old_values=old_values,
        new_values={
            "brand": product.brand.name,
            "model": product.model,
            "sku": product.sku,
            "tracking_method": product.tracking_method,
            "is_active": product.is_active,
        },
    )
    return product
