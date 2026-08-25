from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.core.models import (
    AppendOnlyModel,
    AppendOnlyQuerySet,
    UserStampedModel,
    UUIDPrimaryKeyModel,
)


class UnitStatus(models.TextChoices):
    """docs/architecture/03-status-and-movement-rules.md's status-transition table."""

    IN_STOCK = "in_stock", "In Stock"
    RESERVED = "reserved", "Reserved"
    ASSIGNED = "assigned", "Assigned"
    DELIVERED = "delivered", "Delivered"
    RETURNED = "returned", "Returned"
    DAMAGED = "damaged", "Damaged"
    LOST = "lost", "Lost"
    DISPOSED = "disposed", "Disposed"


class Condition(models.TextChoices):
    NEW = "new", "New"
    GOOD = "good", "Good"
    FAIR = "fair", "Fair"
    DAMAGED = "damaged", "Damaged"
    UNKNOWN = "unknown", "Unknown"


class MovementType(models.TextChoices):
    """docs/architecture/03-status-and-movement-rules.md's movement type reference.
    Only RECEIPT is written by any service as of Phase 3 — the rest are
    listed now so InventoryTransaction's schema/constraint doesn't need a
    migration when Phase 4 adds the services that write them.
    """

    RECEIPT = "receipt", "Receipt into stock"
    TRANSFER = "transfer", "Location transfer"
    RESERVATION = "reservation", "Reservation"
    RESERVATION_RELEASE = "reservation_release", "Reservation release"
    ASSIGNMENT = "assignment", "Employee assignment"
    DELIVERY = "delivery", "Customer delivery"
    RETURN = "return", "Return"
    RETURN_ASSESSMENT = "return_assessment", "Return assessment"
    MARK_DAMAGED = "mark_damaged", "Mark damaged"
    MARK_LOST = "mark_lost", "Mark lost"
    DISPOSAL = "disposal", "Disposal"
    CORRECTION = "correction", "Administrator correction"
    REVERSAL = "reversal", "Reversal"


class UnitAsset(UUIDPrimaryKeyModel, UserStampedModel):
    """One row per physical serialized item. `status`/`current_location` are a
    same-transaction denormalization of the ledger, not an independent cache
    — see docs/architecture/02-data-model.md for why.
    """

    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="unit_assets"
    )
    vendor_serial = models.CharField(max_length=120, blank=True)
    normalized_serial = models.CharField(max_length=120, blank=True, editable=False)
    status = models.CharField(
        max_length=20, choices=UnitStatus.choices, default=UnitStatus.IN_STOCK
    )
    current_location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="unit_assets",
    )
    project_reference = models.CharField(max_length=120, blank=True)
    final_customer = models.CharField(max_length=120, blank=True)
    supplier = models.CharField(max_length=120, blank=True)
    invoice_number = models.CharField(max_length=60, blank=True)
    arrival_date = models.DateField()
    condition = models.CharField(
        max_length=20, choices=Condition.choices, default=Condition.UNKNOWN
    )
    accessories = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    last_removal_date = models.DateField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["normalized_serial"], name="unitasset_serial_idx"),
            models.Index(fields=["product", "status"], name="unitasset_product_status_idx"),
            models.Index(fields=["status"], name="unitasset_status_idx"),
            models.Index(fields=["current_location"], name="unitasset_location_idx"),
            models.Index(fields=["project_reference"], name="unitasset_project_ref_idx"),
            models.Index(fields=["final_customer"], name="unitasset_final_customer_idx"),
            models.Index(fields=["arrival_date"], name="unitasset_arrival_date_idx"),
            models.Index(fields=["last_removal_date"], name="unitasset_removal_date_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=UnitStatus.values), name="unitasset_status_valid"
            ),
        ]

    def __str__(self):
        return f"{self.product} ({self.vendor_serial or 'no serial'})"

    def save(self, *args, **kwargs):
        self.normalized_serial = " ".join((self.vendor_serial or "").split()).upper()
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError

        from apps.catalog.models import TrackingMethod

        if self.product_id and self.product.tracking_method != TrackingMethod.UNIT:
            raise ValidationError({"product": "Unit assets require a unit-tracked product."})

    def get_absolute_url(self):
        return reverse("inventory:asset_detail", kwargs={"pk": self.pk})


class AssetStatusHistory(UUIDPrimaryKeyModel, AppendOnlyModel):
    """A denormalized, append-only per-asset timeline — not an independent
    source of truth. Written in the same transaction as the
    InventoryTransactionLine that causes it (docs/architecture/02-data-model.md).
    """

    unit_asset = models.ForeignKey(
        UnitAsset, on_delete=models.CASCADE, related_name="status_history"
    )
    transaction = models.ForeignKey(
        "InventoryTransaction", on_delete=models.PROTECT, related_name="asset_status_events"
    )
    from_status = models.CharField(max_length=20, choices=UnitStatus.choices, null=True, blank=True)
    to_status = models.CharField(max_length=20, choices=UnitStatus.choices)
    from_location = models.ForeignKey(
        "locations.Location", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    to_location = models.ForeignKey(
        "locations.Location", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    occurred_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    notes = models.TextField(blank=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["occurred_at"]
        indexes = [models.Index(fields=["unit_asset"], name="assetstatus_unit_asset_idx")]
        verbose_name_plural = "asset status history"


class StockBalance(UUIDPrimaryKeyModel):
    """One row per (product, location) — mutated in place, under a row lock,
    only by apps.inventory.services.ledger. Not append-only: it's a running
    balance, immutable ledger lines are what make it reconstructable.
    """

    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="stock_balances"
    )
    location = models.ForeignKey(
        "locations.Location", on_delete=models.PROTECT, related_name="stock_balances"
    )
    on_hand_quantity = models.IntegerField(default=0)
    reserved_quantity = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "location"], name="stockbalance_unique_product_location"
            ),
            models.CheckConstraint(
                condition=models.Q(on_hand_quantity__gte=0), name="stockbalance_on_hand_nonnegative"
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_quantity__gte=0),
                name="stockbalance_reserved_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_quantity__lte=models.F("on_hand_quantity")),
                name="stockbalance_reserved_lte_on_hand",
            ),
        ]
        indexes = [models.Index(fields=["location"], name="stockbalance_location_idx")]

    def __str__(self):
        return f"{self.product} @ {self.location}: {self.on_hand_quantity}"

    @property
    def available_quantity(self):
        return self.on_hand_quantity - self.reserved_quantity

    def get_absolute_url(self):
        return reverse("inventory:balance_detail", kwargs={"pk": self.pk})


class InventoryTransaction(UUIDPrimaryKeyModel, AppendOnlyModel):
    """The ledger header. Rows are INSERT-only; a correction/reversal is a new
    row with `related_transaction` pointing back (docs/architecture/02-data-model.md).
    """

    transaction_number = models.CharField(max_length=20, unique=True, editable=False)
    movement_type = models.CharField(max_length=25, choices=MovementType.choices)
    occurred_at = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="performed_transactions"
    )
    source_location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transactions_from",
    )
    destination_location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transactions_to",
    )
    project_reference = models.CharField(max_length=120, blank=True)
    final_customer = models.CharField(max_length=120, blank=True)
    employee_name = models.CharField(max_length=120, blank=True)
    is_temporary_assignment = models.BooleanField(null=True, blank=True)
    expected_return_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    related_transaction = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="related_transactions"
    )
    duplicate_serial_acknowledged = models.BooleanField(default=False)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["movement_type"], name="txn_movement_type_idx"),
            models.Index(fields=["performed_by"], name="txn_performed_by_idx"),
            models.Index(fields=["occurred_at"], name="txn_occurred_at_idx"),
            models.Index(fields=["source_location"], name="txn_source_location_idx"),
            models.Index(fields=["destination_location"], name="txn_destination_location_idx"),
            models.Index(fields=["project_reference"], name="txn_project_ref_idx"),
            models.Index(fields=["final_customer"], name="txn_final_customer_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(movement_type__in=MovementType.values),
                name="txn_movement_type_valid",
            ),
        ]

    def __str__(self):
        return self.transaction_number

    def get_absolute_url(self):
        return reverse("inventory:transaction_detail", kwargs={"pk": self.pk})


class ReservationStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    RELEASED = "released", "Released"
    CONSUMED = "consumed", "Consumed"


class StockReservation(UUIDPrimaryKeyModel):
    """Tracks *why* a slice of a StockBalance is reserved, carrying Project
    Reference/Final Customer without fragmenting StockBalance itself — see
    docs/architecture/02-data-model.md and doc 10's open item #1. Mutable
    (status transitions active -> released/consumed), unlike the ledger
    tables, but only ever written by apps.inventory.services.
    """

    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="reservations"
    )
    location = models.ForeignKey(
        "locations.Location", on_delete=models.PROTECT, related_name="reservations"
    )
    quantity = models.PositiveIntegerField()
    project_reference = models.CharField(max_length=120, blank=True)
    final_customer = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=20, choices=ReservationStatus.choices, default=ReservationStatus.ACTIVE
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reservation_transaction = models.ForeignKey(
        InventoryTransaction, on_delete=models.PROTECT, related_name="+"
    )
    consuming_transaction = models.ForeignKey(
        InventoryTransaction, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    consumed_quantity = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(
                fields=["product", "location", "status"], name="reservation_loc_status_idx"
            ),
            models.Index(fields=["project_reference"], name="reservation_project_ref_idx"),
            models.Index(fields=["final_customer"], name="reservation_final_cust_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="reservation_quantity_positive"
            ),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.product} @ {self.location} ({self.get_status_display()})"

    def get_absolute_url(self):
        return reverse("inventory:reservation_detail", kwargs={"pk": self.pk})


class InventoryTransactionLine(UUIDPrimaryKeyModel, AppendOnlyModel):
    transaction = models.ForeignKey(
        InventoryTransaction, on_delete=models.PROTECT, related_name="lines"
    )
    line_number = models.PositiveIntegerField()
    unit_asset = models.ForeignKey(
        UnitAsset, null=True, blank=True, on_delete=models.PROTECT, related_name="transaction_lines"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="transaction_lines"
    )
    quantity_delta = models.IntegerField()
    reserved_quantity_delta = models.IntegerField(default=0)
    stock_reservation = models.ForeignKey(
        StockReservation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ledger_lines",
    )
    from_status = models.CharField(max_length=20, choices=UnitStatus.choices, null=True, blank=True)
    to_status = models.CharField(max_length=20, choices=UnitStatus.choices, null=True, blank=True)
    from_location = models.ForeignKey(
        "locations.Location", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    to_location = models.ForeignKey(
        "locations.Location", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    # Snapshot fields — never re-read from Product/UnitAsset after creation
    # (docs/architecture/06-documents-and-snapshots.md).
    brand_snapshot = models.CharField(max_length=120, blank=True)
    model_snapshot = models.CharField(max_length=120, blank=True)
    sku_snapshot = models.CharField(max_length=60, blank=True)
    type_snapshot = models.CharField(max_length=80, blank=True)
    description_snapshot = models.TextField(blank=True)
    serial_snapshot = models.CharField(max_length=120, blank=True)
    project_reference_snapshot = models.CharField(max_length=120, blank=True)
    final_customer_snapshot = models.CharField(max_length=120, blank=True)
    supplier_snapshot = models.CharField(max_length=120, blank=True)
    invoice_number_snapshot = models.CharField(max_length=60, blank=True)
    condition_snapshot = models.CharField(max_length=20, blank=True)
    accessories_snapshot = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["transaction", "line_number"]
        indexes = [
            models.Index(fields=["transaction"], name="txnline_transaction_idx"),
            models.Index(fields=["unit_asset"], name="txnline_unit_asset_idx"),
            models.Index(fields=["product"], name="txnline_product_idx"),
            models.Index(fields=["project_reference_snapshot"], name="txnline_project_ref_idx"),
            models.Index(fields=["final_customer_snapshot"], name="txnline_final_customer_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["transaction", "line_number"], name="txnline_unique_line_number"
            ),
            # The unit-vs-quantity shape check stays local to this table's own
            # columns (unit lines always have quantity_delta in (-1, 1)); the
            # cross-table "product.tracking_method must agree" half of the
            # rule can't be a plain CHECK (needs another table's row) and is
            # enforced solely in services/ledger.py, per doc 02's recommendation.
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(unit_asset__isnull=False)
                        & models.Q(quantity_delta__in=[-1, 1])
                        & models.Q(reserved_quantity_delta=0)
                    )
                    | (
                        models.Q(unit_asset__isnull=True)
                        & (~models.Q(quantity_delta=0) | ~models.Q(reserved_quantity_delta=0))
                    )
                ),
                name="txnline_quantity_delta_valid",
            ),
        ]

    def __str__(self):
        return f"{self.transaction.transaction_number} line {self.line_number}"
