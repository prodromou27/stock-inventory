from django import forms

from apps.catalog.models import Product, TrackingMethod
from apps.locations.models import Location
from apps.locations.scoping import accessible_locations

from .models import Condition


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
