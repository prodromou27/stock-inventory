from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0005_dashboardpreference")]
    operations = [
        migrations.AddField(
            model_name="dashboardpreference",
            name="card_order",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Dashboard card keys in the user's preferred display order.",
            ),
        )
    ]
