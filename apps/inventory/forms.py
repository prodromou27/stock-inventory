from django import forms
from django.forms import formset_factory
from django.utils import timezone

from apps.catalog.models import CATEGORY_TRACKING_METHOD, ItemCategory, Product, TrackingMethod
from apps.locations.models import Location
from apps.locations.scoping import accessible_locations, scope_queryset

from .models import Condition, Customer, StockPurpose, UnitAsset, UnitStatus, WipeMethod


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


class CategoryChoiceSelect(forms.Select):
    """A plain <select> of ItemCategory values, each option carrying
    data-tracking-method derived from CATEGORY_TRACKING_METHOD — reuses
    static/js/movement_forms.js's existing applyTrackingVisibility() (built
    for TrackingMethodSelect above, which looks up an *external* product's
    tracking method) so the same serial/quantity field show-and-hide
    behavior works from a Category pick, without a parallel JS mechanism.
    Category is the one thing an operator actually chooses; tracking method
    is always derived, never asked directly, on this form.
    """

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value not in (None, ""):
            option["attrs"]["data-tracking-method"] = CATEGORY_TRACKING_METHOD[value]
        return option


class ReceiveStockForm(forms.Form):
    """No pre-existing Product is required — Brand/Model/Type are free text
    with <datalist> autocomplete (the same pattern apps.catalog.forms.
    ProductForm/QuickAddProductRowForm already use), resolved via
    apps.catalog.services.resolve_or_create_product() in the view: reused
    silently on an exact match, flagged for acknowledgement on a close
    match, created outright otherwise. SKU stays optional end-to-end.

    Category (not tracking method) is what the Stock Manager actually
    picks — CategoryChoiceSelect derives tracking method from it. For a
    Category that maps to Unit tracking, `vendor_serials` accepts one serial
    per line (blank lines are meaningful here, not stray whitespace to
    discard — see clean()): pasting several real serials receives that many
    units in one submission, and leaving it blank while giving a plain
    `quantity` receives that many individually-tracked units with no serial
    at all, never a fake generated one. For a Category that maps to
    Quantity tracking, `quantity` is the aggregate amount added to the
    balance at `location` and `vendor_serials` is ignored.
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
    category = forms.ChoiceField(
        choices=ItemCategory.choices, label="Category", widget=CategoryChoiceSelect
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
    # based on the selected category (unit-tracked needs the serials box,
    # quantity-tracked needs the quantity field) — purely a UX layer;
    # clean() below remains the actual, unchanged source of truth.
    vendor_serials = forms.CharField(
        required=False,
        label="Serial number(s)",
        help_text="One per line. Leave blank — or add blank lines for some units — when the "
        "physical item has no serial; each still becomes its own tracked unit.",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "One serial per line (optional)",
                "data_tracking_method": "unit",
            }
        ),
    )
    quantity = forms.IntegerField(
        required=False,
        min_value=1,
        label="Quantity",
        help_text="For Quantity-tracked items, the amount received. For Individually-tracked "
        "items, only needed if you didn't list serials above (or to add extra blank-serial "
        "units alongside the ones you did list).",
    )
    project_reference = forms.CharField(max_length=120, required=False, label="Project reference")
    final_customer = forms.CharField(max_length=120, required=False, label="Final customer")
    supplier = forms.CharField(max_length=120, required=False)
    invoice_number = forms.CharField(max_length=60, required=False, label="Invoice number")
    condition = forms.ChoiceField(choices=Condition.choices, required=False, initial=Condition.NEW)
    accessories = forms.CharField(required=False, widget=forms.Textarea)
    notes = forms.CharField(required=False, widget=forms.Textarea)
    # Round-trips through initial -> render -> (possibly invalid) re-render
    # -> review -> confirm using Django's own bound-field machinery, so a
    # validation error never loses the token a fresh GET generated —
    # apps.core.idempotency.claim_submission_token() is only ever actually
    # checked on the final confirmed=true submission.
    submission_token = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_scoped_location(self.fields["location"], user)

    def clean_occurred_at(self):
        value = self.cleaned_data["occurred_at"]
        validate_not_future_date(value)
        return value

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        tracking_method = CATEGORY_TRACKING_METHOD.get(category)
        serial_lines = [line.strip() for line in (cleaned.get("vendor_serials") or "").splitlines()]
        parsed_serials = [line for line in serial_lines if line]
        cleaned["parsed_serials"] = parsed_serials

        if tracking_method == TrackingMethod.QUANTITY:
            if not cleaned.get("quantity"):
                self.add_error("quantity", "Quantity is required for quantity-tracked items.")
        elif tracking_method == TrackingMethod.UNIT:
            entered_quantity = cleaned.get("quantity")
            if parsed_serials:
                if entered_quantity and entered_quantity != len(parsed_serials):
                    self.add_error(
                        "quantity",
                        f"Quantity ({entered_quantity}) doesn't match the number of serials "
                        f"entered ({len(parsed_serials)}). Leave quantity blank to infer it "
                        "from the serials, or set it higher to add extra blank-serial units.",
                    )
                cleaned["unit_count"] = max(entered_quantity or 0, len(parsed_serials))
            else:
                if not entered_quantity:
                    self.add_error(
                        "quantity", "Enter a quantity, or list at least one serial above."
                    )
                cleaned["unit_count"] = entered_quantity

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
    # Round-trips through initial -> render -> (possibly invalid) re-render
    # like ReceiveStockForm's own submission_token — apps.core.idempotency.
    # claim_submission_token() is what actually rejects a reused one; every
    # bulk-capable movement view (Transfer/Reserve/Assign/Deliver/Mark
    # damaged/Mark lost/Dispose) shares this one field via this base class.
    submission_token = forms.CharField(required=False, widget=forms.HiddenInput)

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

    `require_acknowledgement` (set by the view — True for Mark Lost/Dispose,
    False for Mark Damaged) adds a required typed-confirmation checkbox on
    top of the browser-level data-confirm prompt disposition_form.html
    already shows: Lost/Disposed are the two terminal, effectively
    irreversible statuses (VALID_UNIT_TRANSITIONS has no way back out of
    either short of an Administrator correction), so these two get a
    server-validated acknowledgement, not just a JS confirm() dialog a
    script could bypass.
    """

    def __init__(self, *args, user=None, require_acknowledgement=False, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["notes"].required = True
        self.fields["notes"].label = "Reason"
        if require_acknowledgement:
            self.fields["acknowledged"] = forms.BooleanField(
                required=True,
                label="I understand this cannot be easily undone and confirm the selected "
                "item(s) should be marked accordingly.",
            )


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
    category = forms.ChoiceField(
        choices=ItemCategory.choices,
        required=False,
        label="Category",
        widget=CategoryChoiceSelect,
    )
    vendor_serials = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"rows": 3, "placeholder": "One serial per line", "data_tracking_method": "unit"}
        ),
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
        for field_name in ("brand_name", "model", "product_type_name", "category"):
            if not cleaned.get(field_name):
                self.add_error(field_name, "This field is required.")
        tracking_method = CATEGORY_TRACKING_METHOD.get(cleaned.get("category"))
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


class InstallComponentForm(forms.Form):
    """Reached from a Component-category unit asset's own detail page — the
    component itself is fixed (the URL's pk); this only asks which parent
    asset it's being installed into. Both must currently be In Stock at a
    location the operator can access — see
    apps.inventory.services.components' module docstring for why.
    """

    parent_asset = forms.ModelChoiceField(
        queryset=UnitAsset.objects.none(),
        label="Install into",
        widget=forms.Select(attrs={"data-filterable": "true"}),
    )
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, user=None, component=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = scope_queryset(
            user,
            UnitAsset.objects.select_related(
                "product", "product__brand", "current_location"
            ).filter(status=UnitStatus.IN_STOCK),
            location_field="current_location",
        )
        if component is not None:
            queryset = queryset.exclude(pk=component.pk)
        self.fields["parent_asset"].queryset = queryset.order_by(
            "product__brand__name", "product__model"
        )


class RemoveComponentForm(forms.Form):
    """The component (and, implicitly, the parent it's currently installed
    in) is fixed by the URL — this is just the occurred_at/notes every other
    movement asks for.
    """

    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(required=False, widget=forms.Textarea)


ReceiveBulkFormSet = formset_factory(ReceiveBulkLineForm, extra=5)
