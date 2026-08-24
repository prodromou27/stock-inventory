"""Verifies production settings fail closed on missing config, per
docs/architecture/08-nonfunctional-plan.md.

config.settings.production is never the active DJANGO_SETTINGS_MODULE during
tests (that's config.settings.test) — it is imported here as a plain module,
independent of Django's active settings, and reloaded per-test so each test's
monkeypatched environment is picked up.
"""

import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured


def _reload_production_settings():
    import config.settings.production as production_settings

    return importlib.reload(production_settings)


def test_production_settings_require_secret_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("ALLOWED_HOSTS", "example.com")

    with pytest.raises(ImproperlyConfigured):
        _reload_production_settings()


def test_production_settings_require_allowed_hosts(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "a-real-production-secret")
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)

    with pytest.raises(ImproperlyConfigured):
        _reload_production_settings()


def test_production_settings_disable_debug_even_if_env_says_otherwise(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "a-real-production-secret")
    monkeypatch.setenv("ALLOWED_HOSTS", "example.com")
    monkeypatch.setenv("DEBUG", "true")

    module = _reload_production_settings()

    assert module.DEBUG is False
