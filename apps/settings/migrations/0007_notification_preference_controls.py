from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("sysconfig", "0006_alter_notificationdigestdelivery_status")]

    operations = [
        migrations.AddField(
            model_name="notificationsubscription",
            name="mandatory_data_quality",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="notificationsubscription",
            name="mandatory_import_export_failures",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="notificationsubscription",
            name="mandatory_low_stock",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="notificationsubscription",
            name="mandatory_overdue_assignments",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="notificationsubscription",
            name="user_notify_data_quality",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="notificationsubscription",
            name="user_notify_import_export_failures",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="notificationsubscription",
            name="user_notify_low_stock",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="notificationsubscription",
            name="user_notify_overdue_assignments",
            field=models.BooleanField(default=True),
        ),
    ]
