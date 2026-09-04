from django import forms

from apps.catalog.models import Product, TrackingMethod
from apps.locations.models import Location
from apps.locations.scoping import accessible_locations

from .models import Condition, UnitStatus, WipeMethod


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


class ReceiveStockForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(is_active=True).select_related("brand"),
        widget=TrackingMethodSelect(attrs={"data-filterable": "true"}),
    )
    location = forms.ModelChoiceField(queryset=Location.objects.none())
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
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

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        if (
            product
            and product.tracking_method == TrackingMethod.QUANTITY
            and not cleaned.get("quantity")
        ):
            self.add_error("quantity", "Quantity is required for quantity-tracked products.")
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
        widget=forms.DateInput(attrs={"type": "date"}), label="Arrival date"
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


class AssignForm(_QuantityLocationMixin, _BaseMovementForm):
    employee_name = forms.CharField(max_length=120, label="Employee name")
    project_reference = forms.CharField(max_length=120, required=False)
    is_temporary_assignment = forms.BooleanField(required=False, label="Temporary assignment")
    expected_return_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Expected return date"
    )
    condition = forms.ChoiceField(choices=Condition.choices, required=False, initial=Condition.GOOD)
    accessories = forms.CharField(required=False, widget=forms.Textarea)


class DeliverForm(_QuantityLocationMixin, _BaseMovementForm):
    final_customer = forms.CharField(max_length=120, label="Final customer")
    project_reference = forms.CharField(max_length=120, required=False)
    condition = forms.ChoiceField(choices=Condition.choices, required=False, initial=Condition.GOOD)
    accessories = forms.CharField(required=False, widget=forms.Textarea)


class ReturnForm(forms.Form):
    location = forms.ModelChoiceField(queryset=Location.objects.none(), label="Receiving location")
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    condition = forms.ChoiceField(
        choices=Condition.choices, required=False, initial=Condition.UNKNOWN
    )
    accessories = forms.CharField(required=False, widget=forms.Textarea)
    quantity_product = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        required=False,
        label="Quantity product being returned",
        widget=forms.Select(attrs={"data-filterable": "true"}),
    )
    quantity_amount = forms.IntegerField(required=False, min_value=1, label="Quantity returned")
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
    STATUS_CHOICES = UnitStatus.choices

    to_status = forms.ChoiceField(choices=STATUS_CHOICES)
    to_location = forms.ModelChoiceField(queryset=Location.objects.none(), required=False)
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(widget=forms.Textarea)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["to_location"].queryset = Location.objects.filter(is_active=True).order_by(
            "level", "name"
        )


class AdminCorrectBalanceForm(forms.Form):
    new_on_hand_quantity = forms.IntegerField(min_value=0, label="New on-hand quantity")
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(widget=forms.Textarea)


class AdminReversalForm(forms.Form):
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(widget=forms.Textarea)
