from django.db import models

from apps.core.models import UserStampedModel, UUIDPrimaryKeyModel


class ReportBaseModel(models.TextChoices):
    """The fixed allow-list of models an ad-hoc report can be built over —
    apps.reporting.report_builder.REPORTABLE_FIELDS has one entry per value
    here. Never free text; SavedReport.base_model is always one of these.
    """

    UNIT_ASSET = "unit_asset", "Assets"
    STOCK_BALANCE = "stock_balance", "Stock Balances"
    TRANSACTION = "transaction", "Transactions"
    TRANSACTION_LINE = "transaction_line", "Transaction Lines"
    STATUS_HISTORY = "status_history", "Asset Status History"


class SavedReport(UUIDPrimaryKeyModel, UserStampedModel):
    """A user-defined ad-hoc report: which model, which fields, which
    filters. Only ever turned into an actual query by
    apps.reporting.report_builder.build_queryset(), which always applies
    location scoping before anything stored here — this row is never
    trusted as its own authorization boundary. `selected_fields`/`filters`
    are plain JSON (no schema library, same precedent as
    apps.documents.DocumentTemplate/apps.catalog.Product.custom_field_values)
    since they're always re-validated against report_builder's allow-list
    at both save time (apps.reporting.services.create_saved_report()) and
    run time — never executed as stored, unvalidated ORM lookups.

    Any authenticated user may create their own (unshared) saved reports,
    matching this app's existing "reports honor user storage permissions,
    not a separate role gate" pattern (apps.reporting.queries' module
    docstring) — only an Administrator may set is_shared=True, enforced in
    the service layer, not just the form.
    """

    name = models.CharField(max_length=120)
    base_model = models.CharField(max_length=20, choices=ReportBaseModel.choices)
    selected_fields = models.JSONField(default=list, blank=True)
    filters = models.JSONField(default=list, blank=True)
    is_shared = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
