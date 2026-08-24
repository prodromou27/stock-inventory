from django import forms

from .models import TrackingMethod


class ProductForm(forms.Form):
    """A plain Form, not a ModelForm — create/update go through services.py
    so validation, duplicate detection, and audit logic live in one place.
    Brand/Type are free-text and resolved to Brand/ProductType rows via
    get-or-create in the service (docs/architecture/05-tracking-and-duplicates.md).
    """

    brand_name = forms.CharField(max_length=120, label="Brand")
    model = forms.CharField(max_length=120, label="Model")
    sku = forms.CharField(max_length=60, required=False, label="SKU")
    product_type_name = forms.CharField(max_length=80, label="Type/category")
    description = forms.CharField(required=False, widget=forms.Textarea)
    tracking_method = forms.ChoiceField(choices=TrackingMethod.choices)
    supplier = forms.CharField(max_length=120, required=False)
    default_notes = forms.CharField(required=False, widget=forms.Textarea)
    low_stock_threshold = forms.IntegerField(required=False, min_value=0)
    is_active = forms.BooleanField(required=False, initial=True)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("tracking_method") != TrackingMethod.QUANTITY:
            cleaned["low_stock_threshold"] = None
        return cleaned
