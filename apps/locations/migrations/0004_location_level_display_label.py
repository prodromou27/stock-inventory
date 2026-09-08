from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("locations", "0003_alter_location_level")]
    operations = [
        migrations.AlterField(
            model_name="location",
            name="level",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("country", "Country"),
                    ("storage_room", "Storage Room"),
                    ("rack_shelf", "Floor (Shelf/Rack)"),
                ],
            ),
        ),
    ]
