import pytest
from django.urls import reverse

from apps.exports.models import ExportSchedule, ExportSettings


@pytest.mark.django_db
class TestExportSettingsPermissions:
    def test_anonymous_redirected(self, client):
        assert client.get(reverse("exports:settings")).status_code == 302

    def test_stock_manager_forbidden(self, client, stock_manager):
        client.force_login(stock_manager)
        assert client.get(reverse("exports:settings")).status_code == 403

    def test_read_only_forbidden(self, client, read_only_user):
        client.force_login(read_only_user)
        assert client.get(reverse("exports:settings")).status_code == 403

    def test_administrator_can_view(self, client, administrator):
        client.force_login(administrator)
        response = client.get(reverse("exports:settings"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestExportSettingsForm:
    def test_saves_valid_settings(self, client, administrator, tmp_path):
        client.force_login(administrator)
        response = client.post(
            reverse("exports:settings"),
            {"export_path": str(tmp_path), "schedule": "nightly", "weekly_weekday": "2"},
        )
        assert response.status_code == 302

        settings_obj = ExportSettings.load()
        assert settings_obj.export_path == str(tmp_path)
        assert settings_obj.schedule == ExportSchedule.NIGHTLY

    def test_rejects_bad_path_with_form_error(self, client, administrator, unwritable_path):
        client.force_login(administrator)
        response = client.post(
            reverse("exports:settings"),
            {
                "export_path": unwritable_path,
                "schedule": "nightly",
                "weekly_weekday": "6",
            },
        )
        assert response.status_code == 200
        assert "not writable" in response.content.decode()

    def test_stock_manager_cannot_save(self, client, stock_manager, tmp_path):
        client.force_login(stock_manager)
        response = client.post(
            reverse("exports:settings"),
            {"export_path": str(tmp_path), "schedule": "nightly", "weekly_weekday": "6"},
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestRunExportNow:
    def test_writes_a_real_file_and_shows_success_message(
        self, client, administrator, unit_product, location_tree, tmp_path
    ):
        from datetime import date

        from apps.inventory.services.receipts import receive_stock

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-RUNNOW-1",
        )
        client.force_login(administrator)
        client.post(
            reverse("exports:settings"),
            {"export_path": str(tmp_path), "schedule": "nightly", "weekly_weekday": "6"},
        )

        response = client.post(reverse("exports:run_now"), follow=True)
        assert response.status_code == 200
        assert "Export written to" in response.content.decode()
        assert list(tmp_path.glob("stock_inventory_backup_*.xlsx"))

    def test_shows_error_message_on_failure(self, client, administrator, tmp_path, unwritable_path):
        client.force_login(administrator)
        client.post(
            reverse("exports:settings"),
            {"export_path": str(tmp_path), "schedule": "nightly", "weekly_weekday": "6"},
        )
        settings_obj = ExportSettings.load()
        settings_obj.export_path = unwritable_path
        settings_obj.save(update_fields=["export_path"])

        response = client.post(reverse("exports:run_now"), follow=True)
        assert response.status_code == 200
        assert "Export failed" in response.content.decode()

    def test_stock_manager_cannot_trigger(self, client, stock_manager):
        client.force_login(stock_manager)
        assert client.post(reverse("exports:run_now")).status_code == 403
