from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("locations", "0004_location_level_display_label")]
    operations = [
        migrations.AlterField(
            model_name="location",
            name="level",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("country", "Country"),
                    ("storage_room", "Storage Room"),
                    ("rack_shelf", "Shelf/Rack"),
                ],
            ),
        ),
    ]
