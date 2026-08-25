import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.exports.models import ExportSchedule, ExportSettings
from apps.exports.services import update_settings


@pytest.mark.django_db
def test_noop_when_disabled(administrator, capsys, tmp_path):
    update_settings(
        user=administrator, export_path="", schedule=ExportSchedule.DISABLED, weekly_weekday=6
    )
    call_command("run_scheduled_export")
    assert "nothing to do" in capsys.readouterr().out
    assert not list(tmp_path.glob("*.xlsx"))


@pytest.mark.django_db
def test_runs_when_nightly(administrator, capsys, tmp_path):
    update_settings(
        user=administrator,
        export_path=str(tmp_path),
        schedule=ExportSchedule.NIGHTLY,
        weekly_weekday=6,
    )
    call_command("run_scheduled_export")
    assert "written to" in capsys.readouterr().out
    assert list(tmp_path.glob("stock_inventory_backup_*.xlsx"))


@pytest.mark.django_db
def test_raises_command_error_on_failure(administrator, tmp_path, unwritable_path):
    update_settings(
        user=administrator,
        export_path=str(tmp_path),
        schedule=ExportSchedule.NIGHTLY,
        weekly_weekday=6,
    )
    settings_obj = ExportSettings.load()
    settings_obj.export_path = unwritable_path
    settings_obj.save(update_fields=["export_path"])

    with pytest.raises(CommandError):
        call_command("run_scheduled_export")
