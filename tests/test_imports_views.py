import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.imports import services
from apps.imports.models import ImportBatchStatus, ImportRowOutcome

COLUMNS = services.parsing.COLUMNS


def _csv_upload(rows, filename="test.csv"):
    lines = [",".join(COLUMNS)]
    for row in rows:
        values = [str(row.get(col, "")) for col in COLUMNS]
        lines.append(",".join(values))
    content = ("\n".join(lines) + "\n").encode("utf-8")
    return SimpleUploadedFile(filename, content, content_type="text/csv")


def _base_row(**overrides):
    row = {
        "BRAND": "Fortinet",
        "MODEL/Part No./SKU": "FG-100F",
        "TYPE/DESCRIPTION": "Firewall",
        "S/N": "SNVIEW001",
        "QTY": "1",
        "LOCATION": "Room A",
    }
    row.update(overrides)
    return row


@pytest.mark.django_db
class TestPermissions:
    def test_anonymous_redirected_from_every_view(self, client):
        for url in [
            reverse("imports:batch_list"),
            reverse("imports:upload"),
            reverse("imports:template_download"),
        ]:
            assert client.get(url).status_code == 302

    def test_stock_manager_forbidden(self, client, stock_manager):
        client.force_login(stock_manager)
        assert client.get(reverse("imports:batch_list")).status_code == 403
        assert client.get(reverse("imports:upload")).status_code == 403

    def test_read_only_forbidden(self, client, read_only_user):
        client.force_login(read_only_user)
        assert client.get(reverse("imports:batch_list")).status_code == 403


@pytest.mark.django_db
class TestUploadAndPreview:
    def test_administrator_can_upload_and_preview(self, client, administrator, location_tree):
        client.force_login(administrator)
        upload = _csv_upload([_base_row(LOCATION="Room A")])
        response = client.post(reverse("imports:upload"), {"file": upload})
        assert response.status_code == 302

        detail_response = client.get(response.url)
        assert detail_response.status_code == 200
        assert "SNVIEW001" in detail_response.content.decode()

    def test_rejects_unsupported_extension(self, client, administrator):
        client.force_login(administrator)
        bad_file = SimpleUploadedFile("data.txt", b"not a spreadsheet", content_type="text/plain")
        response = client.post(reverse("imports:upload"), {"file": bad_file})
        assert response.status_code == 200
        assert "Only .xlsx or .csv" in response.content.decode()

    def test_preview_shows_resolved_arrival_date_for_a_blank_cell(
        self, client, administrator, location_tree
    ):
        from django.utils import timezone

        client.force_login(administrator)
        upload = _csv_upload([_base_row(LOCATION="Room A")])  # no Arrival Date supplied
        response = client.post(reverse("imports:upload"), {"file": upload})
        detail_response = client.get(response.url)
        body = detail_response.content.decode()
        assert timezone.localdate().isoformat() in body
        assert "defaulted to today" in body


@pytest.mark.django_db
class TestExecuteAndDownloads:
    def test_execute_then_download_results(self, client, administrator, location_tree):
        client.force_login(administrator)
        upload = _csv_upload([_base_row(LOCATION="Room A")])
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)

        execute_response = client.post(reverse("imports:execute", args=[batch.pk]))
        assert execute_response.status_code == 302

        batch.refresh_from_db()
        assert batch.status == ImportBatchStatus.COMPLETED

        results_response = client.get(reverse("imports:results_download", args=[batch.pk]))
        assert results_response.status_code == 200
        assert results_response["Content-Type"] == "text/csv"
        assert "SNVIEW001" in results_response.content.decode()

    def test_template_download(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("imports:template_download"))
        assert response.status_code == 200
        assert response.content.decode().startswith("BRAND,MODEL/Part No./SKU")

    def test_row_override_location_then_execute(self, client, administrator, location_tree):
        client.force_login(administrator)
        upload = _csv_upload([_base_row(LOCATION="Nowhere Real")])
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        row = batch.rows.get()
        assert row.outcome == ImportRowOutcome.WARNING

        override_response = client.post(
            reverse("imports:row_override_location", args=[batch.pk, row.pk]),
            {"location": location_tree["room"].pk},
        )
        assert override_response.status_code == 302

        client.post(reverse("imports:execute", args=[batch.pk]))
        batch.refresh_from_db()
        assert batch.status == ImportBatchStatus.COMPLETED

    def test_row_skip(self, client, administrator, location_tree):
        client.force_login(administrator)
        upload = _csv_upload([_base_row(LOCATION="Room A")])
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        row = batch.rows.get()

        skip_response = client.post(reverse("imports:row_skip", args=[batch.pk, row.pk]))
        assert skip_response.status_code == 302

        row.refresh_from_db()
        assert row.outcome == ImportRowOutcome.SKIPPED

    def test_stock_manager_cannot_execute(
        self, client, stock_manager, administrator, location_tree
    ):
        upload = _csv_upload([_base_row(LOCATION="Room A")])
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)

        client.force_login(stock_manager)
        response = client.post(reverse("imports:execute", args=[batch.pk]))
        assert response.status_code == 403

    def test_template_xlsx_download(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("imports:template_xlsx_download"))
        assert response.status_code == 200
        assert response["Content-Type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_execute_blocked_when_repeat_of_completed_until_confirmed(
        self, client, administrator, location_tree
    ):
        upload_content = _csv_upload([_base_row(LOCATION="Room A", **{"S/N": "SN-REPEAT-1"})])
        first_batch, _ = services.create_batch_from_upload(
            uploaded_file=upload_content, user=administrator
        )
        services.execute_batch(batch=first_batch, user=administrator)
        first_batch.refresh_from_db()
        assert first_batch.status == ImportBatchStatus.COMPLETED

        # A second upload with byte-identical content.
        repeat_upload = SimpleUploadedFile(
            "test.csv",
            first_batch.file.read(),
            content_type="text/csv",
        )
        second_batch, is_repeat = services.create_batch_from_upload(
            uploaded_file=repeat_upload, user=administrator
        )
        assert is_repeat is True

        client.force_login(administrator)
        response = client.post(reverse("imports:execute", args=[second_batch.pk]))
        assert response.status_code == 302
        second_batch.refresh_from_db()
        assert second_batch.status == ImportBatchStatus.PREVIEWED  # not executed

        confirmed_response = client.post(
            reverse("imports:execute", args=[second_batch.pk]),
            {"confirm_repeat_upload": "true"},
        )
        assert confirmed_response.status_code == 302
        second_batch.refresh_from_db()
        # The row's own serial now duplicates the first batch's already-imported
        # asset, so it's held for an explicit per-row acknowledgement (separate,
        # pre-existing protection) rather than fully completing — the point
        # here is that the repeat-upload gate no longer blocks execution once
        # confirmed, not that every row succeeds.
        assert second_batch.status != ImportBatchStatus.PREVIEWED
