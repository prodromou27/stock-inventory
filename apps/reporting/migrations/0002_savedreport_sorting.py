from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reporting", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="savedreport",
            name="sort_by",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="savedreport",
            name="sort_direction",
            field=models.CharField(
                choices=[("asc", "Ascending"), ("desc", "Descending")],
                default="asc",
                max_length=4,
            ),
        ),
    ]
