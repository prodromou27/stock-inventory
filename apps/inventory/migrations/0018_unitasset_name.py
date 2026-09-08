from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0017_alter_inventorytransaction_wipe_method")]
    operations = [
        migrations.AddField(
            model_name="unitasset",
            name="name",
            field=models.CharField(blank=True, max_length=160),
        ),
    ]
