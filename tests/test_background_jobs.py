from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.jobs import (
    cleanup_expired_outputs,
    enqueue_report,
    fail_stale_jobs,
    process_next_job,
)
from apps.core.models import BackgroundJob
from apps.reporting.models import ReportBaseModel
from apps.reporting.services import create_saved_report


@pytest.fixture
def saved_asset_report(administrator):
    return create_saved_report(
        user=administrator,
        name="Asset register",
        base_model=ReportBaseModel.UNIT_ASSET,
        selected_fields=["serial", "brand"],
        filters=[],
    )


@pytest.mark.django_db
class TestBackgroundJobs:
    def test_worker_builds_downloadable_scoped_report(self, administrator, saved_asset_report):
        job, created = enqueue_report(
            user=administrator, report=saved_asset_report, export_format="csv"
        )

        assert created is True
        assert process_next_job().pk == job.pk
        job.refresh_from_db()
        assert job.status == BackgroundJob.Status.SUCCEEDED
        assert job.progress_percent == 100
        assert job.output_filename == "asset-register.csv"
        assert job.output_size_bytes > 0
        assert job.output_file.open("rb").read().startswith(b"\xef\xbb\xbf")
        assert job.output_expires_at > timezone.now()

    def test_identical_active_job_is_idempotent(self, administrator, saved_asset_report):
        first, first_created = enqueue_report(
            user=administrator, report=saved_asset_report, export_format="xlsx"
        )
        second, second_created = enqueue_report(
            user=administrator, report=saved_asset_report, export_format="xlsx"
        )

        assert first_created is True
        assert second_created is False
        assert second.pk == first.pk

    def test_job_pages_do_not_expose_another_users_output(
        self, client, administrator, read_only_user, saved_asset_report
    ):
        job, _ = enqueue_report(user=administrator, report=saved_asset_report, export_format="csv")
        process_next_job()

        client.force_login(read_only_user)
        assert client.get(reverse("core:job_detail", kwargs={"pk": job.pk})).status_code == 404
        assert client.get(reverse("core:job_download", kwargs={"pk": job.pk})).status_code == 404

    def test_expired_output_is_deleted_but_history_remains(self, administrator, saved_asset_report):
        job, _ = enqueue_report(user=administrator, report=saved_asset_report, export_format="csv")
        process_next_job()
        job.refresh_from_db()
        stored_name = job.output_file.name
        storage = job.output_file.storage
        BackgroundJob.objects.filter(pk=job.pk).update(
            output_expires_at=timezone.now() - timedelta(seconds=1)
        )

        assert cleanup_expired_outputs() == 1
        job.refresh_from_db()
        assert job.status == BackgroundJob.Status.SUCCEEDED
        assert job.output_deleted_at is not None
        assert not job.output_file
        assert not storage.exists(stored_name)

    def test_worker_persists_failure_instead_of_crashing_queue(
        self, administrator, saved_asset_report
    ):
        job, _ = enqueue_report(user=administrator, report=saved_asset_report, export_format="pdf")
        administrator.is_active = False
        administrator.save(update_fields=["is_active"])

        process_next_job()
        job.refresh_from_db()
        assert job.status == BackgroundJob.Status.FAILED
        assert "no longer active" in job.error_message

    def test_abandoned_running_job_is_failed_without_automatic_retry(
        self, administrator, saved_asset_report
    ):
        job, _ = enqueue_report(user=administrator, report=saved_asset_report, export_format="csv")
        BackgroundJob.objects.filter(pk=job.pk).update(
            status=BackgroundJob.Status.RUNNING,
            started_at=timezone.now() - timedelta(hours=7),
        )

        assert fail_stale_jobs() == 1
        job.refresh_from_db()
        assert job.status == BackgroundJob.Status.FAILED
        assert "retry" in job.error_message.lower()


@pytest.mark.django_db
def test_saved_report_export_button_queues_job(client, administrator, saved_asset_report):
    client.force_login(administrator)
    response = client.post(
        reverse("reporting:saved_report_export", kwargs={"pk": saved_asset_report.pk}),
        {"format": "csv"},
    )
    job = BackgroundJob.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("core:job_detail", kwargs={"pk": job.pk})
    assert job.status == BackgroundJob.Status.QUEUED
