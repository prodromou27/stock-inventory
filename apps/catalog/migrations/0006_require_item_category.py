import apps.catalog.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0005_classify_existing_products"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="category",
            field=models.CharField(choices=apps.catalog.models.ItemCategory.choices, max_length=20),
        ),
    ]
