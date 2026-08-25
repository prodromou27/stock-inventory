from django import forms

from apps.catalog.models import Product, TrackingMethod
from apps.locations.models import Location
from apps.locations.scoping import accessible_locations

from .models import Condition, UnitStatus


class ReceiveStockForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(is_active=True).select_related("brand")
    )
    location = forms.ModelChoiceField(queryset=Location.objects.none())
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    vendor_serial = forms.CharField(max_length=120, required=False, label="Vendor serial")
    quantity = forms.IntegerField(required=False, min_value=1)
    project_reference = forms.CharField(max_length=120, required=False, label="Project reference")
    final_customer = forms.CharField(max_length=120, required=False, label="Final customer")
    supplier = forms.CharField(max_length=120, required=False)
    invoice_number = forms.CharField(max_length=60, required=False, label="Invoice number")
    condition = forms.ChoiceField(choices=Condition.choices, required=False, initial=Condition.NEW)
    accessories = forms.CharField(required=False, widget=forms.Textarea)
    notes = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = (
            accessible_locations(user).filter(is_active=True).order_by("level", "name")
        )

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


class _BaseMovementForm(forms.Form):
    """Shared by every movement form below: a scoped location field for a
    single optional quantity line, plus occurred_at/notes. Unit-asset
    selection is handled outside this form (a checkbox list rendered from
    the view's eligible-assets queryset — see views.py), since it can't be
    expressed as a static form field.
    """

    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    quantity_product = forms.ModelChoiceField(
        queryset=Product.objects.filter(is_active=True, tracking_method=TrackingMethod.QUANTITY),
        required=False,
        label="Quantity product (optional)",
    )
    quantity_amount = forms.IntegerField(required=False, min_value=1, label="Quantity")
    notes = forms.CharField(required=False, widget=forms.Textarea)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity_product") and not cleaned.get("quantity_amount"):
            self.add_error("quantity_amount", "Enter a quantity for the selected product.")
        return cleaned


class TransferForm(_BaseMovementForm):
    destination_location = forms.ModelChoiceField(queryset=Location.objects.none())
    quantity_source_location = forms.ModelChoiceField(
        queryset=Location.objects.none(), required=False, label="Quantity source location"
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        locs = accessible_locations(user).filter(is_active=True).order_by("level", "name")
        self.fields["destination_location"].queryset = locs
        self.fields["quantity_source_location"].queryset = locs

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity_product") and not cleaned.get("quantity_source_location"):
            self.add_error(
                "quantity_source_location", "Select the source location for the quantity line."
            )
        return cleaned


class ReserveForm(_BaseMovementForm):
    project_reference = forms.CharField(max_length=120)
    final_customer = forms.CharField(max_length=120, required=False)
    quantity_location = forms.ModelChoiceField(
        queryset=Location.objects.none(), required=False, label="Quantity location"
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity_location"].queryset = (
            accessible_locations(user).filter(is_active=True).order_by("level", "name")
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity_product") and not cleaned.get("quantity_location"):
            self.add_error("quantity_location", "Select a location for the quantity line.")
        return cleaned


class AssignForm(_BaseMovementForm):
    employee_name = forms.CharField(max_length=120, label="Employee name")
    project_reference = forms.CharField(max_length=120, required=False)
    is_temporary_assignment = forms.BooleanField(required=False, label="Temporary assignment")
    expected_return_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Expected return date"
    )
    condition = forms.ChoiceField(choices=Condition.choices, required=False, initial=Condition.GOOD)
    accessories = forms.CharField(required=False, widget=forms.Textarea)
    quantity_location = forms.ModelChoiceField(
        queryset=Location.objects.none(), required=False, label="Quantity location"
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity_location"].queryset = (
            accessible_locations(user).filter(is_active=True).order_by("level", "name")
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity_product") and not cleaned.get("quantity_location"):
            self.add_error("quantity_location", "Select a location for the quantity line.")
        return cleaned


class DeliverForm(_BaseMovementForm):
    final_customer = forms.CharField(max_length=120, label="Final customer")
    project_reference = forms.CharField(max_length=120, required=False)
    condition = forms.ChoiceField(choices=Condition.choices, required=False, initial=Condition.GOOD)
    accessories = forms.CharField(required=False, widget=forms.Textarea)
    quantity_location = forms.ModelChoiceField(
        queryset=Location.objects.none(), required=False, label="Quantity location"
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity_location"].queryset = (
            accessible_locations(user).filter(is_active=True).order_by("level", "name")
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity_product") and not cleaned.get("quantity_location"):
            self.add_error("quantity_location", "Select a location for the quantity line.")
        return cleaned


class ReturnForm(forms.Form):
    location = forms.ModelChoiceField(queryset=Location.objects.none(), label="Receiving location")
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    condition = forms.ChoiceField(
        choices=Condition.choices, required=False, initial=Condition.UNKNOWN
    )
    accessories = forms.CharField(required=False, widget=forms.Textarea)
    quantity_product = forms.ModelChoiceField(
        queryset=Product.objects.none(), required=False, label="Quantity product being returned"
    )
    quantity_amount = forms.IntegerField(required=False, min_value=1, label="Quantity returned")
    notes = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, user=None, quantity_product_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = (
            accessible_locations(user).filter(is_active=True).order_by("level", "name")
        )
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


class DispositionForm(_BaseMovementForm):
    """Shared shape for mark-damaged/mark-lost/dispose — notes doubles as the
    required reason (spec §9: "record the reason, notes, date...").
    """

    quantity_location = forms.ModelChoiceField(
        queryset=Location.objects.none(), required=False, label="Quantity location"
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity_location"].queryset = (
            accessible_locations(user).filter(is_active=True).order_by("level", "name")
        )
        self.fields["notes"].required = True
        self.fields["notes"].label = "Reason"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("quantity_product") and not cleaned.get("quantity_location"):
            self.add_error("quantity_location", "Select a location for the quantity line.")
        return cleaned


class RepairDamagedForm(forms.Form):
    location = forms.ModelChoiceField(queryset=Location.objects.none())
    occurred_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(required=False, widget=forms.Textarea, label="Repair notes")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = (
            accessible_locations(user).filter(is_active=True).order_by("level", "name")
        )


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
