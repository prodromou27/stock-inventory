from django import forms
from django.forms import formset_factory

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


class QuickAddProductRowForm(forms.Form):
    """One row of apps.catalog.views.QuickAddProductsView's formset — the
    fields needed to create a product fast; anything else (description,
    default notes, low-stock threshold) is a follow-up edit on the created
    product, not part of the bulk-add path. A completely blank row is
    silently skipped, not an error.

    "Blank" is judged from brand_name/model/product_type_name specifically
    (see clean() below), not Django's own has_changed()-based
    empty_permitted skip — tracking_method is a <select> that always
    submits *some* value once rendered, which made has_changed() report a
    change (hence "blank" fields as validation errors) for rows the
    operator never actually touched.
    """

    brand_name = forms.CharField(max_length=120, required=False, label="Brand")
    model = forms.CharField(max_length=120, required=False, label="Model")
    sku = forms.CharField(max_length=60, required=False, label="SKU")
    product_type_name = forms.CharField(max_length=80, required=False, label="Type")
    tracking_method = forms.ChoiceField(choices=TrackingMethod.choices, required=False)
    supplier = forms.CharField(max_length=120, required=False)

    def clean(self):
        cleaned = super().clean()
        identifying_fields = (
            cleaned.get("brand_name"),
            cleaned.get("model"),
            cleaned.get("product_type_name"),
        )
        if not any(identifying_fields):
            return cleaned  # nothing entered on this row — silently skipped
        for field in ("brand_name", "model", "product_type_name"):
            if not cleaned.get(field):
                self.add_error(field, "Required.")
        if not cleaned.get("tracking_method"):
            cleaned["tracking_method"] = TrackingMethod.UNIT
        return cleaned


QuickAddProductFormSet = formset_factory(QuickAddProductRowForm, extra=10)
