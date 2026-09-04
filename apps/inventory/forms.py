from django import forms
from django.forms import formset_factory
from django.utils import timezone

from apps.catalog.models import Product, TrackingMethod
from apps.locations.models import Location
from apps.locations.scoping import accessible_locations

from .models import Condition, Customer, StockPurpose, UnitStatus, WipeMethod


def validate_not_future_date(value):
    """Arrival Date may be any date up to and including today (historical
    stock is expected — old inventory being entered late), but never a date
    that hasn't happened yet. Scoped to receiving's Arrival Date only, not
    every movement form's occurred_at — see the plan's phase 3 note.
    """
    if value and value > timezone.localdate():
        raise forms.ValidationError("Arrival date cannot be in the future.")


def _scoped_location_queryset(user):
    """Active locations `user` can access, ordered for a predictable
    <select> — the queryset every movement form's location-shaped field
    needs; was hand-copied in every form's __init__ before being factored
    out here.
    """
    return accessible_locations(user).filter(is_active=True).order_by("level", "name")


def _apply_scoped_location(field, user):
    """Sets a location ModelChoiceField's queryset to what `user` can
    access, and marks its widget filterable — static/js/movement_forms.js
    adds a type-to-filter box above any <select data-filterable> with
    enough options to be worth filtering. The <select> itself is
    unchanged; this is additive markup only.
    """
    field.queryset = _scoped_location_queryset(user)
    field.widget.attrs["data-filterable"] = "true"


class TrackingMethodSelect(forms.Select):
    """A plain <select> whose <option>s additionally carry
    data-tracking-method="unit"/"quantity" — read by
    static/js/movement_forms.js to show/hide fields that only apply to one
    tracking method (e.g. ReceiveStockForm's vendor_serial vs quantity).
    The <select>'s own submitted value is completely unaffected; this is
    additive markup only, computed once per render (a single pk->tracking_
    method dict from the field's own queryset), not a query per option.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tracking_by_pk = None

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value in (None, ""):
            return option
        if self._tracking_by_pk is None:
            self._tracking_by_pk = dict(self.choices.queryset.values_list("pk", "tracking_method"))
        pk = getattr(value, "value", value)
        tracking_method = self._tracking_by_pk.get(pk)
        if tracking_method:
            option["attrs"]["data-tracking-method"] = tracking_method
        return option


class TrackingChoiceSelect(forms.Select):
    """A plain <select> of the two TrackingMethod values themselves, each
    option carrying data-tracking-method equal to its own value — reuses
    static/js/movement_forms.js's existing applyTrackingVisibility() (built
    for TrackingMethodSelect above, which looks up an *external* product's
    tracking method) for the Add Stock case, where there's no product yet to
    look one up from: the operator picks the tracking method directly.
    """

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value not in (None, ""):
            option["attrs"]["data-tracking-method"] = value
        return option


class ReceiveStockForm(forms.Form):
    """No pre-existing Product is required — Brand/Model/Type are free text
    with <datalist> autocomplete (the same pattern apps.catalog.forms.
    ProductForm/QuickAddProductRowForm already use), resolved via
    apps.catalog.services.resolve_or_create_product() in the view: reused
    silently on an exact match, flagged for acknowledgement on a close
    match, created outright otherwise. SKU stays optional end-to-end.
    """

    brand_name = forms.CharField(
        max_length=120,
        label="Brand",
        widget=forms.TextInput(attrs={"list": "brand-options", "autocomplete": "off"}),
    )
    model = forms.CharField(max_length=120, label="Model")
    sku = forms.CharField(max_length=60, required=False, label="SKU (optional)")
    product_type_name = forms.CharField(
        max_length=80,
        label="Type",
        widget=forms.TextInput(attrs={"list": "product-type-options", "autocomplete": "off"}),
    )
    tracking_method = forms.ChoiceField(
        choices=TrackingMethod.choices, label="Tracking method", widget=TrackingChoiceSelect
    )
    location = forms.ModelChoiceField(queryset=Location.objects.none())
    occurred_at = forms.DateField(
        label="Arrival date",
        widget=forms.DateInput(attrs={"type": "date", "data-arrival-date-field": "true"}),
    )
    stock_purpose = forms.ChoiceField(
        choices=StockPurpose.choices,
        required=False,
        initial=StockPurpose.INTERNAL,
        label="Stock purpose",
    )
    # data-tracking-method: static/js/movement_forms.js shows/hides these two
    # based on the selected product (unit-tracked needs a serial, quantity-
    # tracked needs a quantity) — purely a UX layer; clean() below remains
    # the actual, unchanged source of truth.
    vendor_serial = forms.CharField(
        max_length=120,
        required=False,
        label="Vendor serial",
        widget=forms.TextInput(attrs={"data_tracking_method": "unit"}),
    )
    quantity = forms.IntegerField(
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"data_tracking_method": "quantity"}),
    )
    project_reference = forms.CharField(max_length=120, required=False, label="Project reference")
    final_customer = forms.CharField(max_length=120, required=False, label="Final customer")
    supplier = forms.CharField(max_length=120, required=False)
    invoice_number = forms.CharField(max_length=60, required=False, label="Invoice number")
    condition = forms.ChoiceField(choices=Condition.choices, required=False, initial=Condition.NEW)
    accessories = forms.CharField(required=False, widget=forms.Textarea)
    notes = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_scoped_location(self.fields["location"], user)

    def clean_occurred_at(self):
        value = self.cleaned_data["occurred_at"]
        validate_not_future_date(value)
        return value

    def clean(self):
        cleaned = super().clean()
        tracking_method = cleaned.get("tracking_method")
        if tracking_method == TrackingMethod.QUANTITY and not cleaned.get("quantity"):
            self.add_error("quantity", "Quantity is required for quantity-tracked products.")
        if tracking_method == TrackingMethod.UNIT and not cleaned.get("vendor_serial"):
            self.add_error("vendor_serial", "Vendor serial is required for unit-tracked products.")
        cleaned["stock_purpose"] = cleaned.get("stock_purpose") or StockPurpose.INTERNAL
        if cleaned["stock_purpose"] == StockPurpose.CUSTOMER and not cleaned.get("final_customer"):
            self.add_error("final_customer", "Final customer is required for Customer stock.")
        return cleaned


class QuickReceiveForm(forms.Form):
    """apps.inventory.services.receipts.receive_stock_batch() — one row per
    non-blank line of `vendor_serials`, all sharing every other field here.
    """

    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(
            is_active=True, tracking_method=TrackingMethod.UNIT
        ).select_related("brand"),
        label="Product",
        widget=forms.Select(attrs={"data-filterable": "true"}),
    )
    location = forms.ModelChoiceField(queryset=Location.objects.none())
    occurred_at = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "data-arrival-date-field": "true"}),
        label="Arrival date",
    )
    stock_purpose = forms.ChoiceField(
        choices=StockPurpose.choices,
        required=False,
        initial=StockPurpose.INTERNAL,
        label="Stock purpose",
    )
    vendor_serials = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 10, "placeholder": "One serial per line"}),
        label="Serials",
        help_text="One per line — blank lines are ignored.",
        # required=False so a whitespace-only submission reaches
        # clean_vendor_serials() below for the friendlier "enter at least
        # one serial" message, instead of CharField's own required check
        # (which runs on the post-strip value and would otherwise fire
        # first with a generic "This field is required.").
        required=False,
    )
    project_reference = forms.CharField(max_length=120, required=False, label="Project reference")
    final_customer = forms.CharField(max_length=120, required=False, label="Final customer")
    supplier = forms.CharField(max_length=120, required=False)
    invoice_number = forms.CharField(max_length=60, required=False, label="Invoice number")
    condition = forms.ChoiceField(choices=Condition.choices, required=False, initial=Condition.NEW)
    accessories = forms.CharField(required=False, widget=forms.Textarea)
    notes = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_scoped_location(self.fields["location"], user)

    def clean_vendor_serials(self):
        lines = self.cleaned_data["vendor_serials"].splitlines()
        if not any(line.strip() for line in lines):
            raise forms.ValidationError("Enter at least one serial.")
        return lines

    def clean_occurred_at(self):
        value = self.cleaned_data["occurred_at"]
        validate_not_future_date(value)
        return value

    def clean(self):
        cleaned = super().clean()
        cleaned["stock_purpose"] = cleaned.get("stock_purpose") or StockPurpose.INTERNAL
        if cleaned["stock_purpose"] == StockPurpose.CUSTOMER and not cleaned.get("final_customer"):
            self.add_error("final_customer", "Final customer is required for Customer stock.")
        return cleaned


class _BaseMovementForm(forms.Form):
    """Shared by every movement form below: a scoped location field for a
    single optional quantity line, plus occurred_at/notes. Unit-asset
    selection is handled outside this form (a checkbox list rendered from
    the view's eligible-assets queryset — see views.py), since it can't be
    expressed as a static form field.

    Accepts (and, on its own, ignores) `user=` so that both direct
    subclasses (which don't need it) and subclasses combined with
    _QuantityLocationMixin (which does) can uniformly pass it through
    super().__init__(..., user=user) without a TypeError either way.
    """

    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    quantity_product = forms.ModelChoiceField(
        queryset=Product.objects.filter(is_active=True, tracking_method=TrackingMethod.QUANTITY),
        required=False,
        label="Quantity product (optional)",
        widget=forms.Select(attrs={"data-filterable": "true"}),
    )
    quantity_amount = forms.IntegerField(required=False, min_value=1, label="Quantity")
    notes = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity_product") and not cleaned.get("quantity_amount"):
            self.add_error("quantity_amount", "Enter a quantity for the selected product.")
        return cleaned


class _QuantityLocationMixin(forms.Form):
    """Adds an optional `quantity_location` field — plus its scoped
    queryset and its "quantity_product needs quantity_location" check — to
    a _BaseMovementForm subclass. Reserve/Assign/Deliver/Disposition all
    needed this identically; Transfer doesn't (it has its own, differently-
    scoped quantity_source_location instead), so this isn't on the shared
    base itself. Must come before _BaseMovementForm in the MRO (i.e.
    `class X(_QuantityLocationMixin, _BaseMovementForm)`), so its
    super().__init__()/clean() calls chain into the base correctly.

    Subclasses forms.Form (not a plain mixin) because Django's form
    metaclass only collects a class's declared fields into base_fields
    when that class is itself part of the forms.Form metaclass chain — a
    plain-object mixin's field attributes are silently never registered.
    """

    quantity_location = forms.ModelChoiceField(
        queryset=Location.objects.none(), required=False, label="Quantity location"
    )
    quantity_stock_purpose = forms.ChoiceField(
        choices=StockPurpose.choices,
        required=False,
        initial=StockPurpose.INTERNAL,
        label="Stock purpose",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        _apply_scoped_location(self.fields["quantity_location"], user)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity_product") and not cleaned.get("quantity_location"):
            self.add_error("quantity_location", "Select a location for the quantity line.")
        return cleaned


class TransferForm(_BaseMovementForm):
    destination_location = forms.ModelChoiceField(queryset=Location.objects.none())
    quantity_source_location = forms.ModelChoiceField(
        queryset=Location.objects.none(), required=False, label="Quantity source location"
    )
    quantity_stock_purpose = forms.ChoiceField(
        choices=StockPurpose.choices,
        required=False,
        initial=StockPurpose.INTERNAL,
        label="Stock purpose",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        _apply_scoped_location(self.fields["destination_location"], user)
        _apply_scoped_location(self.fields["quantity_source_location"], user)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity_product") and not cleaned.get("quantity_source_location"):
            self.add_error(
                "quantity_source_location", "Select the source location for the quantity line."
            )
        return cleaned


class ReserveForm(_QuantityLocationMixin, _BaseMovementForm):
    project_reference = forms.CharField(max_length=120)
    final_customer = forms.CharField(max_length=120, required=False)


class AssignForm(_BaseMovementForm):
    """Deliberately not _QuantityLocationMixin — per direct instruction, the
    Stock Manager picks specific items (unit assets from _asset_picker.html,
    quantity rows from _balance_picker.html) and enters only the date and
    the employee's name/reference; location, stock purpose, and product are
    all implicit in *which item/row was selected*, never a separate
    dropdown. See views._quantity_lines_from_balance_picker().
    """

    employee_name = forms.CharField(max_length=120, label="Employee name")
    recipient_reference = forms.CharField(
        max_length=120, required=False, label="Employee reference (optional)"
    )
    project_reference = forms.CharField(max_length=120, required=False)
    is_temporary_assignment = forms.BooleanField(required=False, label="Temporary assignment")
    expected_return_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Expected return date"
    )
    condition = forms.ChoiceField(choices=Condition.choices, required=False, initial=Condition.USED)
    accessories = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        del self.fields["quantity_product"]
        del self.fields["quantity_amount"]


class DeliverForm(_BaseMovementForm):
    """final_customer stays the historical name snapshot recorded on the
    transaction and remains fully free text (spec §22: never a hard,
    required lookup) — customer is an optional live reference to a matching
    Customer row, set by static/js/movement_forms.js when the typed text
    exactly matches a name/reference the customer search datalist offered.
    """

    final_customer = forms.CharField(
        max_length=120,
        label="Final customer",
        widget=forms.TextInput(
            attrs={"list": "customer-options", "autocomplete": "off", "data-customer-field": "true"}
        ),
    )
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.filter(is_active=True),
        required=False,
        widget=forms.HiddenInput(attrs={"data-customer-id-field": "true"}),
    )
    recipient_reference = forms.CharField(
        max_length=120, required=False, label="Customer reference (optional)"
    )
    project_reference = forms.CharField(max_length=120, required=False)
    condition = forms.ChoiceField(choices=Condition.choices, required=False, initial=Condition.USED)
    accessories = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        del self.fields["quantity_product"]
        del self.fields["quantity_amount"]


class ReturnForm(forms.Form):
    location = forms.ModelChoiceField(queryset=Location.objects.none(), label="Receiving location")
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    condition = forms.ChoiceField(choices=Condition.choices, required=False, initial=Condition.USED)
    accessories = forms.CharField(required=False, widget=forms.Textarea)
    quantity_product = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        required=False,
        label="Quantity product being returned",
        widget=forms.Select(attrs={"data-filterable": "true"}),
    )
    quantity_amount = forms.IntegerField(required=False, min_value=1, label="Quantity returned")
    quantity_stock_purpose = forms.ChoiceField(
        choices=StockPurpose.choices,
        required=False,
        initial=StockPurpose.INTERNAL,
        label="Stock purpose",
    )
    notes = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, user=None, quantity_product_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_scoped_location(self.fields["location"], user)
        self.fields["quantity_product"].queryset = (
            quantity_product_choices or Product.objects.none()
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity_amount") and not cleaned.get("quantity_product"):
            self.add_error(
                "quantity_product", "Select which quantity-tracked product is being returned."
            )
        return cleaned


class ReturnAssessmentForm(forms.Form):
    ASSESSMENT_CHOICES = [
        (UnitStatus.IN_STOCK, "In Stock"),
        (UnitStatus.DAMAGED, "Damaged"),
        (UnitStatus.DISPOSED, "Disposed"),
    ]

    to_status = forms.ChoiceField(choices=ASSESSMENT_CHOICES, label="Resolve to")
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(required=False, widget=forms.Textarea)


class DispositionForm(_QuantityLocationMixin, _BaseMovementForm):
    """Shared shape for mark-damaged/mark-lost/dispose — notes doubles as the
    required reason (spec §9: "record the reason, notes, date...").
    """

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["notes"].required = True
        self.fields["notes"].label = "Reason"


class DisposeForm(DispositionForm):
    """DispositionForm plus the two fields the disposal certificate needs
    (apps.documents.pdf's document skeleton renders them when present) —
    only Dispose asks for these, not Mark damaged/Mark lost, so this is a
    subclass rather than fields added to the shared DispositionForm.
    """

    wipe_method = forms.ChoiceField(choices=WipeMethod.choices, label="Storage media wipe method")
    witness_name = forms.CharField(required=False, label="Witnessed by (optional)")


class RepairDamagedForm(forms.Form):
    location = forms.ModelChoiceField(queryset=Location.objects.none())
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(required=False, widget=forms.Textarea, label="Repair notes")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_scoped_location(self.fields["location"], user)


class AdminCorrectUnitForm(forms.Form):
    """Also the only path to change arrival_date after receipt — ordinary
    editing must never touch it (spec-adjacent request), so it's exposed
    here, optional, alongside the existing status/location correction,
    rather than as a second separate correction screen.
    """

    STATUS_CHOICES = UnitStatus.choices

    to_status = forms.ChoiceField(choices=STATUS_CHOICES)
    to_location = forms.ModelChoiceField(queryset=Location.objects.none(), required=False)
    arrival_date = forms.DateField(
        required=False,
        label="Correct arrival date (optional)",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(widget=forms.Textarea)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["to_location"].queryset = Location.objects.filter(is_active=True).order_by(
            "level", "name"
        )

    def clean_arrival_date(self):
        value = self.cleaned_data["arrival_date"]
        validate_not_future_date(value)
        return value


class AdminCorrectBalanceForm(forms.Form):
    new_on_hand_quantity = forms.IntegerField(min_value=0, label="New on-hand quantity")
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(widget=forms.Textarea)


class AdminReversalForm(forms.Form):
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(widget=forms.Textarea)


class UnitPurposeReclassifyForm(forms.Form):
    """apps.inventory.services.purpose.reclassify_unit_purpose() — relabels a
    single serialized asset's Stock Purpose. `new_purpose` excludes the
    asset's current value in the view (see UnitPurposeReclassifyView), since
    reclassifying to the same purpose is a no-op the service itself rejects.
    """

    new_purpose = forms.ChoiceField(choices=StockPurpose.choices, label="New stock purpose")
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(widget=forms.Textarea)


class QuantityPurposeReclassifyForm(forms.Form):
    """apps.inventory.services.purpose.reclassify_quantity_purpose() — moves
    `quantity` of a quantity-tracked product between two Stock Purpose
    buckets at one location.
    """

    from_purpose = forms.ChoiceField(choices=StockPurpose.choices, label="From")
    to_purpose = forms.ChoiceField(choices=StockPurpose.choices, label="To")
    quantity = forms.IntegerField(min_value=1)
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(widget=forms.Textarea)

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("from_purpose")
            and cleaned.get("to_purpose")
            and cleaned["from_purpose"] == cleaned["to_purpose"]
        ):
            self.add_error("to_purpose", "Source and destination stock purpose must differ.")
        return cleaned


class ReceiveBulkBatchForm(forms.Form):
    """Batch-level defaults for apps.inventory.views.ReceiveBulkView — shared
    across every line of ReceiveBulkFormSet unless a row overrides
    location/stock_purpose ("apply one location to the batch or override it
    for individual items").
    """

    occurred_at = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "data-arrival-date-field": "true"}),
        label="Arrival date",
    )
    default_location = forms.ModelChoiceField(
        queryset=Location.objects.none(), label="Default location"
    )
    default_stock_purpose = forms.ChoiceField(
        choices=StockPurpose.choices,
        initial=StockPurpose.INTERNAL,
        label="Default stock purpose",
    )
    supplier = forms.CharField(max_length=120, required=False)
    invoice_number = forms.CharField(max_length=60, required=False, label="Invoice number")
    project_reference = forms.CharField(max_length=120, required=False, label="Project reference")
    final_customer = forms.CharField(max_length=120, required=False, label="Final customer")
    notes = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_scoped_location(self.fields["default_location"], user)

    def clean_occurred_at(self):
        value = self.cleaned_data["occurred_at"]
        validate_not_future_date(value)
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("default_stock_purpose") == StockPurpose.CUSTOMER and not cleaned.get(
            "final_customer"
        ):
            self.add_error("final_customer", "Final customer is required for Customer stock.")
        return cleaned


class ReceiveBulkLineForm(forms.Form):
    """One row of ReceiveBulkFormSet — a single product line in a multi-
    product goods receipt (apps.inventory.services.receipts.
    receive_stock_bulk()). `location`/`stock_purpose` are optional per-row
    overrides of ReceiveBulkBatchForm's batch-level default. A completely
    blank row (no product selected) is silently skipped, matching
    apps.catalog.forms.QuickAddProductFormSet's convention.
    """

    brand_name = forms.CharField(
        max_length=120,
        required=False,
        label="Brand",
        widget=forms.TextInput(attrs={"list": "brand-options", "autocomplete": "off"}),
    )
    model = forms.CharField(max_length=120, required=False, label="Model")
    sku = forms.CharField(max_length=60, required=False, label="SKU (optional)")
    product_type_name = forms.CharField(
        max_length=80,
        required=False,
        label="Type",
        widget=forms.TextInput(attrs={"list": "product-type-options", "autocomplete": "off"}),
    )
    tracking_method = forms.ChoiceField(
        choices=TrackingMethod.choices,
        required=False,
        label="Tracking method",
        widget=TrackingChoiceSelect,
    )
    vendor_serials = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "One serial per line"}),
        label="Serials",
    )
    quantity = forms.IntegerField(required=False, min_value=1)
    location = forms.ModelChoiceField(
        queryset=Location.objects.none(), required=False, label="Location override"
    )
    stock_purpose = forms.ChoiceField(
        choices=StockPurpose.choices, required=False, label="Stock purpose override"
    )
    arrival_date_override = forms.DateField(
        required=False,
        label="Arrival date override",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    condition = forms.ChoiceField(choices=Condition.choices, required=False, initial=Condition.NEW)
    accessories = forms.CharField(required=False, widget=forms.Textarea)
    notes = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_scoped_location(self.fields["location"], user)

    def clean_arrival_date_override(self):
        value = self.cleaned_data["arrival_date_override"]
        validate_not_future_date(value)
        return value

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("brand_name") and not cleaned.get("model"):
            return cleaned  # blank row — silently skipped, not an error
        for field_name in ("brand_name", "model", "product_type_name", "tracking_method"):
            if not cleaned.get(field_name):
                self.add_error(field_name, "This field is required.")
        tracking_method = cleaned.get("tracking_method")
        if tracking_method == TrackingMethod.UNIT:
            serials = [
                s.strip() for s in (cleaned.get("vendor_serials") or "").splitlines() if s.strip()
            ]
            if not serials:
                self.add_error("vendor_serials", "Enter at least one serial for this product.")
            cleaned["parsed_serials"] = serials
        elif tracking_method == TrackingMethod.QUANTITY and not cleaned.get("quantity"):
            self.add_error("quantity", "Quantity is required for quantity-tracked products.")
        return cleaned


ReceiveBulkFormSet = formset_factory(ReceiveBulkLineForm, extra=5)
