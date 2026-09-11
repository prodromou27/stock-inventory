from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("documents", "0012_documenttemplate_custom_html_enabled")]

    operations = [
        migrations.AddField(
            model_name="generateddocument",
            name="sha256",
            field=models.CharField(
                blank=True,
                editable=False,
                help_text="Content digest captured before the immutable PDF snapshot is stored.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="generateddocument",
            name="size_bytes",
            field=models.PositiveBigIntegerField(
                blank=True,
                editable=False,
                help_text="Stored PDF size captured at generation time; null for legacy snapshots.",
                null=True,
            ),
        ),
    ]
