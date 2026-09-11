import apps.core.models
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_dashboardpreference_card_order"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BackgroundJob",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "task_type",
                    models.CharField(
                        choices=[
                            ("document", "Generate document"),
                            ("import", "Execute import"),
                            ("report", "Export custom report"),
                            ("inventory_export", "Export full inventory"),
                            ("notifications", "Refresh notifications"),
                        ],
                        max_length=24,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                        ],
                        default="queued",
                        max_length=12,
                    ),
                ),
                ("payload", models.JSONField(default=dict)),
                ("progress_percent", models.PositiveSmallIntegerField(default=0)),
                ("status_message", models.CharField(blank=True, max_length=255)),
                ("error_message", models.TextField(blank=True)),
                ("result_url", models.CharField(blank=True, max_length=500)),
                (
                    "output_file",
                    models.FileField(
                        blank=True, upload_to=apps.core.models._background_job_output_path
                    ),
                ),
                ("output_content_type", models.CharField(blank=True, max_length=100)),
                ("output_filename", models.CharField(blank=True, max_length=255)),
                ("output_size_bytes", models.PositiveBigIntegerField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("output_expires_at", models.DateTimeField(blank=True, null=True)),
                ("output_deleted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="background_jobs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="backgroundjob",
            index=models.Index(fields=["status", "created_at"], name="bgjob_status_created_idx"),
        ),
        migrations.AddIndex(
            model_name="backgroundjob",
            index=models.Index(
                fields=["requested_by", "-created_at"], name="bgjob_user_created_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="backgroundjob",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ("queued", "running"))),
                fields=("requested_by", "task_type", "payload"),
                name="bgjob_unique_active_request",
            ),
        ),
    ]
