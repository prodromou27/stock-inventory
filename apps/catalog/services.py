from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, require_role
from apps.core.text import normalize_whitespace

from .models import (
    Brand,
    Product,
    ProductCustomFieldDefinition,
    ProductCustomFieldType,
    ProductType,
    TrackingMethod,
)


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
def create_custom_field_definition(*, user, name, field_type, display_order=0):
    require_role(user, ADMINISTRATOR)

    name = normalize_whitespace(name)
    if not name:
        raise ValidationError("Name is required.")
    if field_type not in ProductCustomFieldType.values:
        raise ValidationError("Unknown field type.")

    definition = ProductCustomFieldDefinition(
        name=name, field_type=field_type, display_order=display_order
    )
    definition.full_clean()
    definition.save()
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_CREATED,
        obj=definition,
        summary=f"Created product custom field '{definition.name}'",
        new_values={"name": definition.name, "field_type": definition.field_type},
    )
    return definition


@transaction.atomic
def set_custom_field_definition_active(*, definition, user, is_active):
    require_role(user, ADMINISTRATOR)

    if definition.is_active == is_active:
        return definition

    definition.is_active = is_active
    definition.save(update_fields=["is_active", "updated_at"])
    verb = "Activated" if is_active else "Deactivated"
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=definition,
        summary=f"{verb} product custom field '{definition.name}'",
        old_values={"is_active": not is_active},
        new_values={"is_active": is_active},
    )
    return definition


def _coerce_custom_field_value(value, field_type):
    if value in (None, ""):
        return None
    if field_type == ProductCustomFieldType.DATE and hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _active_custom_field_definitions():
    return {str(d.pk): d for d in ProductCustomFieldDefinition.objects.filter(is_active=True)}


def _validate_custom_field_values(raw_values, active_definitions):
    """Keeps only keys present in `active_definitions`, coercing each value
    into the JSON-safe shape Product.custom_field_values expects (a form
    field's cleaned_data can hand back a datetime.date, which JSONField
    can't store directly).

    An unrecognized key (a stale/deactivated definition, or a crafted
    payload calling this directly rather than through the dynamically
    built ProductForm) is silently dropped rather than raising — the same
    allow-list philosophy as apps.core.sorting.SortableListMixin: only
    ever act on keys explicitly known to be valid right now.
    """
    if not raw_values:
        return {}
    cleaned = {}
    for key, value in raw_values.items():
        definition = active_definitions.get(key)
        if definition is None:
            continue
        coerced = _coerce_custom_field_value(value, definition.field_type)
        if coerced is not None:
            cleaned[key] = coerced
    return cleaned


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
    custom_field_values=None,
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
        custom_field_values=_validate_custom_field_values(
            custom_field_values, _active_custom_field_definitions()
        ),
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
    custom_field_values=None,
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

    # A value stored for a definition that's since gone inactive is left
    # untouched (it wasn't part of this submission — the form only ever
    # renders active definitions); every currently-active key reflects
    # exactly what was just submitted, including being cleared if blank.
    active_definitions = _active_custom_field_definitions()
    merged_custom_fields = {
        key: value
        for key, value in (product.custom_field_values or {}).items()
        if key not in active_definitions
    }
    merged_custom_fields.update(
        _validate_custom_field_values(custom_field_values, active_definitions)
    )

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
    product.custom_field_values = merged_custom_fields
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
