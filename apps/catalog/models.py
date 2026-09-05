from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.urls import reverse

from apps.core.models import TimestampedModel, UserStampedModel, UUIDPrimaryKeyModel
from apps.core.text import normalize_whitespace


class Brand(UUIDPrimaryKeyModel, TimestampedModel):
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            # Trigram index (apps.core's pg_trgm extension) backing typo-tolerant
            # ranking in apps.core.views.GlobalSearchView — icontains alone can't
            # use this index (Django #32803), so the view also annotates with
            # TrigramSimilarity, which can.
            GinIndex(fields=["name"], name="brand_name_trgm_idx", opclasses=["gin_trgm_ops"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = normalize_whitespace(self.name)
        super().save(*args, **kwargs)


class ProductType(UUIDPrimaryKeyModel, TimestampedModel):
    name = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "product type"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = normalize_whitespace(self.name)
        super().save(*args, **kwargs)


class TrackingMethod(models.TextChoices):
    UNIT = "unit", "Unit (serialized)"
    QUANTITY = "quantity", "Quantity"


class ItemCategory(models.TextChoices):
    """How an item is physically tracked and used — orthogonal to
    inventory.StockPurpose (internal/customer, "who it's for") and
    inventory.UnitStatus ("where it is right now"), the same way that pair
    is already documented as orthogonal to each other. Category is the one
    of the three an operator actually thinks in terms of when receiving
    stock; TrackingMethod stays a real stored column underneath it (see
    CATEGORY_TRACKING_METHOD) since ~40 existing call sites already branch
    on tracking_method and category maps onto exactly those same two values.
    """

    SERIALIZED_ASSET = "serialized_asset", "Serialized Asset"
    QUANTITY_STOCK = "quantity_stock", "Quantity Stock"
    CONSUMABLE = "consumable", "Consumable"
    REUSABLE_ACCESSORY = "reusable_accessory", "Reusable Accessory"
    COMPONENT = "component", "Component"


# Fixed, enforced derivation — never a free user choice independent of
# category (apps.catalog.services.create_product() is the single place that
# reads this to set Product.tracking_method from Product.category).
CATEGORY_TRACKING_METHOD = {
    ItemCategory.SERIALIZED_ASSET: TrackingMethod.UNIT,
    ItemCategory.REUSABLE_ACCESSORY: TrackingMethod.UNIT,
    ItemCategory.COMPONENT: TrackingMethod.UNIT,
    ItemCategory.QUANTITY_STOCK: TrackingMethod.QUANTITY,
    ItemCategory.CONSUMABLE: TrackingMethod.QUANTITY,
}


class ProductCustomFieldType(models.TextChoices):
    TEXT = "text", "Text"
    NUMBER = "number", "Number"
    DATE = "date", "Date"
    BOOLEAN = "boolean", "Yes/No"


class ProductCustomFieldDefinition(UUIDPrimaryKeyModel, TimestampedModel):
    """An Administrator-defined extra field on Product (e.g. "Warranty
    expiry"), added from Settings without a code deployment. Values live in
    Product.custom_field_values, a plain JSONField keyed by this row's pk
    (stable across a later rename) — no dynamic-schema library is used;
    apps.catalog.services validates/coerces values against active
    definitions at save time.

    Never hard-deleted or edited once created — `field_type` changing after
    values exist would make those stored values meaningless, and this
    codebase's established pattern for exactly that situation (see
    Product.tracking_method) is to lock rather than allow a silent
    reinterpretation. `is_active=False` removes it from the product form
    going forward without discarding values already stored for it.
    """

    name = models.CharField(max_length=80, unique=True)
    field_type = models.CharField(max_length=10, choices=ProductCustomFieldType.choices)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


class Product(UUIDPrimaryKeyModel, UserStampedModel):
    """docs/architecture/02-data-model.md's Product entity. `tracking_method`
    is immutable once the product has any movement — enforced in services.py,
    not here (a fresh Product always starts unlocked).
    """

    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="products")
    model = models.CharField(max_length=120)
    sku = models.CharField(max_length=60, blank=True)
    normalized_model = models.CharField(max_length=120, editable=False, blank=True)
    normalized_sku = models.CharField(max_length=60, editable=False, blank=True)
    product_type = models.ForeignKey(ProductType, on_delete=models.PROTECT, related_name="products")
    description = models.TextField(blank=True)
    category = models.CharField(max_length=20, choices=ItemCategory.choices)
    tracking_method = models.CharField(max_length=10, choices=TrackingMethod.choices)
    supplier = models.CharField(max_length=120, blank=True)
    default_notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    low_stock_threshold = models.PositiveIntegerField(null=True, blank=True)
    custom_field_values = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["normalized_model", "normalized_sku"], name="product_normalized_idx"
            ),
            models.Index(fields=["brand"], name="product_brand_idx"),
            models.Index(fields=["product_type"], name="product_type_idx"),
            models.Index(fields=["is_active"], name="product_active_idx"),
            # Trigram indexes backing GlobalSearchView's TrigramSimilarity ranking —
            # see Brand.Meta's index for why icontains alone can't use these.
            GinIndex(fields=["model"], name="product_model_trgm_idx", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["sku"], name="product_sku_trgm_idx", opclasses=["gin_trgm_ops"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    ~models.Q(tracking_method=TrackingMethod.UNIT)
                    | models.Q(low_stock_threshold__isnull=True)
                ),
                name="product_low_stock_threshold_unit_null",
            ),
            # CATEGORY_TRACKING_METHOD's mapping enforced at the DB layer too —
            # category is nullable only during the migration-0004/0005/0006
            # backfill window (see those migrations); every row written by
            # the service layer from here on always has both set in agreement.
            models.CheckConstraint(
                condition=(
                    models.Q(category__isnull=True)
                    | (
                        models.Q(
                            category__in=[
                                ItemCategory.SERIALIZED_ASSET,
                                ItemCategory.REUSABLE_ACCESSORY,
                                ItemCategory.COMPONENT,
                            ]
                        )
                        & models.Q(tracking_method=TrackingMethod.UNIT)
                    )
                    | (
                        models.Q(
                            category__in=[
                                ItemCategory.QUANTITY_STOCK,
                                ItemCategory.CONSUMABLE,
                            ]
                        )
                        & models.Q(tracking_method=TrackingMethod.QUANTITY)
                    )
                ),
                name="product_category_tracking_method_agree",
            ),
        ]

    def __str__(self):
        label = f"{self.brand.name} {self.model}"
        return f"{label} ({self.sku})" if self.sku else label

    def save(self, *args, **kwargs):
        self.model = normalize_whitespace(self.model)
        self.sku = normalize_whitespace(self.sku)
        self.normalized_model = self.model.lower()
        self.normalized_sku = self.sku.lower()
        if self.tracking_method != TrackingMethod.QUANTITY:
            self.low_stock_threshold = None
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("catalog:product_detail", kwargs={"pk": self.pk})

    def has_movements(self):
        """True once any ledger line references this product — the point at
        which tracking_method becomes locked (docs/architecture/05-tracking-and-duplicates.md).
        """
        return self.transaction_lines.exists()
