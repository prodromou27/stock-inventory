from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("sysconfig", "0005_alter_systemsettings_site_name")]
    operations = [
        migrations.AlterField(
            model_name="notificationdigestdelivery",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("sent", "Sent"),
                    ("no_content", "No content"),
                    ("failed", "Failed"),
                ],
                max_length=12,
            ),
        )
    ]
