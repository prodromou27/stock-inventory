import pytest

from apps.settings.models import SystemSettings


@pytest.mark.django_db
class TestSystemSettingsLoad:
    def test_returns_unsaved_defaults_when_no_row_exists(self):
        settings_obj = SystemSettings.load()
        assert settings_obj.pk == 1
        assert settings_obj._state.adding is True
        assert settings_obj.site_name == "Stock Inventory"

    def test_does_not_create_a_row_on_read(self):
        SystemSettings.load()
        assert SystemSettings.objects.count() == 0

    def test_returns_the_saved_row_once_one_exists(self):
        SystemSettings.objects.create(pk=1, site_name="Acme Inventory")
        settings_obj = SystemSettings.load()
        assert settings_obj._state.adding is False
        assert settings_obj.site_name == "Acme Inventory"


@pytest.mark.django_db
class TestAllowedHostsList:
    def test_blank_override_is_an_empty_list(self):
        settings_obj = SystemSettings(allowed_hosts_override="")
        assert settings_obj.allowed_hosts_list == []

    def test_splits_and_strips_comma_separated_hosts(self):
        settings_obj = SystemSettings(allowed_hosts_override=" example.com, other.example.com ,")
        assert settings_obj.allowed_hosts_list == ["example.com", "other.example.com"]
