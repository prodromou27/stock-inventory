import django.conf
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(django.conf.settings.AUTH_USER_MODEL),
        ("imports", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="importrow",
            name="duplicate_serial_acknowledged",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="importrow",
            name="duplicate_serial_acknowledged_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="importrow",
            name="duplicate_serial_acknowledged_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=django.conf.settings.AUTH_USER_MODEL,
            ),
        ),
    ]
