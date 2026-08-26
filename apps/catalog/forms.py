from django import forms
from django.forms import formset_factory

from .models import ProductCustomFieldDefinition, ProductCustomFieldType, TrackingMethod

CUSTOM_FIELD_PREFIX = "custom_field_"


def custom_field_key(definition_id):
    return f"{CUSTOM_FIELD_PREFIX}{definition_id}"


def _custom_field_form_field(definition):
    common = {"required": False, "label": definition.name}
    if definition.field_type == ProductCustomFieldType.NUMBER:
        return forms.FloatField(**common)
    if definition.field_type == ProductCustomFieldType.DATE:
        return forms.DateField(**common, widget=forms.DateInput(attrs={"type": "date"}))
    if definition.field_type == ProductCustomFieldType.BOOLEAN:
        return forms.BooleanField(**common)
    return forms.CharField(max_length=500, **common)


class ProductForm(forms.Form):
    """A plain Form, not a ModelForm — create/update go through services.py
    so validation, duplicate detection, and audit logic live in one place.
    Brand/Type are free-text and resolved to Brand/ProductType rows via
    get-or-create in the service (docs/architecture/05-tracking-and-duplicates.md).

    One extra field is appended per active ProductCustomFieldDefinition
    (apps.catalog.views custom-field admin screens) — never hardcoded here,
    since the whole point is an Administrator can add these without a code
    change. get_custom_field_values() (call only after is_valid()) hands
    back the {definition_id: value} dict services.update_product()/
    create_product() expect.
    """

    brand_name = forms.CharField(
        max_length=120,
        label="Brand",
        widget=forms.TextInput(attrs={"list": "brand-options", "autocomplete": "off"}),
    )
    model = forms.CharField(max_length=120, label="Model")
    sku = forms.CharField(max_length=60, required=False, label="SKU")
    product_type_name = forms.CharField(
        max_length=80,
        label="Type/category",
        widget=forms.TextInput(attrs={"list": "product-type-options", "autocomplete": "off"}),
    )
    description = forms.CharField(required=False, widget=forms.Textarea)
    tracking_method = forms.ChoiceField(choices=TrackingMethod.choices)
    supplier = forms.CharField(max_length=120, required=False)
    default_notes = forms.CharField(required=False, widget=forms.Textarea)
    low_stock_threshold = forms.IntegerField(required=False, min_value=0)
    is_active = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.custom_field_definitions = list(
            ProductCustomFieldDefinition.objects.filter(is_active=True)
        )
        for definition in self.custom_field_definitions:
            self.fields[custom_field_key(definition.pk)] = _custom_field_form_field(definition)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("tracking_method") != TrackingMethod.QUANTITY:
            cleaned["low_stock_threshold"] = None
        return cleaned

    def get_custom_field_values(self):
        return {
            str(definition.pk): self.cleaned_data.get(custom_field_key(definition.pk))
            for definition in self.custom_field_definitions
        }


class ProductCustomFieldDefinitionForm(forms.Form):
    name = forms.CharField(max_length=80)
    field_type = forms.ChoiceField(choices=ProductCustomFieldType.choices)
    display_order = forms.IntegerField(required=False, min_value=0, initial=0)

    def clean_display_order(self):
        return self.cleaned_data.get("display_order") or 0


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

    brand_name = forms.CharField(
        max_length=120,
        required=False,
        label="Brand",
        widget=forms.TextInput(attrs={"list": "brand-options", "autocomplete": "off"}),
    )
    model = forms.CharField(max_length=120, required=False, label="Model")
    sku = forms.CharField(max_length=60, required=False, label="SKU")
    product_type_name = forms.CharField(
        max_length=80,
        required=False,
        label="Type",
        widget=forms.TextInput(attrs={"list": "product-type-options", "autocomplete": "off"}),
    )
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
