"""Safely classifies every existing Product by its current tracking_method —
UNIT -> Serialized Asset, QUANTITY -> Quantity Stock — per the direct
instruction to classify existing records from current data rather than
discard or reinterpret it. An Administrator can reclassify any individual
product afterward (e.g. to Reusable Accessory or Component) via the normal
product-edit screen; this migration only guarantees every row leaves with
*some* correct, non-destructive classification.
"""

from django.db import migrations


def classify_products(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Product.objects.filter(tracking_method="unit", category__isnull=True).update(
        category="serialized_asset"
    )
    Product.objects.filter(tracking_method="quantity", category__isnull=True).update(
        category="quantity_stock"
    )


def noop_reverse(apps, schema_editor):
    """Deliberately not reversed — an Administrator may have already
    reclassified some products (e.g. to Reusable Accessory) by the time
    anyone reverses this migration; blanking category back out would
    discard real, intentional data, not just this migration's own default.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_add_item_category"),
    ]

    operations = [
        migrations.RunPython(classify_products, noop_reverse),
    ]
