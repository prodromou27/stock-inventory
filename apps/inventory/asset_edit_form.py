from django import forms

from .models import UnitAsset
from .services.asset_editing import EDIT_FIELDS


class AssetEditForm(forms.ModelForm):
    brand_name = forms.CharField(max_length=120, label="Brand")
    model = forms.CharField(max_length=120)
    sku = forms.CharField(max_length=60, required=False, label="SKU")
    duplicate_serial_acknowledged = forms.BooleanField(
        required=False, label="I confirm this is a separate item with a duplicate serial"
    )
    duplicate_product_acknowledged = forms.BooleanField(
        required=False, label="I confirm this is a distinct product despite similar catalog entries"
    )

    class Meta:
        model = UnitAsset
        fields = EDIT_FIELDS
        labels = {"name": "Asset name"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.update(
            brand_name=self.instance.product.brand.name,
            model=self.instance.product.model,
            sku=self.instance.product.sku,
        )
