from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0003_initial")]

    operations = [
        migrations.CreateModel(
            name="SubmissionClaim",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("token", models.UUIDField(unique=True)),
                ("claimed_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
        )
    ]
