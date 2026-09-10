from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0008_remove_product_product_brand_idx_and_more")]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="category",
            field=models.CharField(
                choices=[
                    ("serialized_asset", "Serialized Asset"),
                    ("quantity_stock", "Counted Stock"),
                    ("consumable", "Consumable"),
                    ("reusable_accessory", "Reusable Accessory"),
                    ("component", "Component"),
                ],
                max_length=20,
            ),
        ),
    ]
