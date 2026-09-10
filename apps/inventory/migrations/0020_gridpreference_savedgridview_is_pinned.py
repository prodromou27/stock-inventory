import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0019_internal_use"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="savedgridview",
            name="is_pinned",
            field=models.BooleanField(
                default=False,
                help_text="Show this personal view as a shortcut in the stock workspace.",
            ),
        ),
        migrations.CreateModel(
            name="GridPreference",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("grid_key", models.CharField(max_length=20)),
                ("state", models.JSONField(blank=True, default=dict)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="grid_preferences",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "grid_key"),
                        name="gridpreference_unique_user_grid",
                    )
                ]
            },
        ),
    ]
