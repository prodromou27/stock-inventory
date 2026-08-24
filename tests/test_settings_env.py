import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.env import env_bool, env_int, env_list, env_str


def test_env_str_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_VAR", raising=False)
    assert env_str("SOME_VAR", default="fallback") == "fallback"


def test_env_str_required_raises_when_missing(monkeypatch):
    monkeypatch.delenv("SOME_VAR", raising=False)
    with pytest.raises(ImproperlyConfigured):
        env_str("SOME_VAR", required=True)


def test_env_bool_parses_truthy_and_falsy_values(monkeypatch):
    monkeypatch.setenv("FLAG", "true")
    assert env_bool("FLAG") is True
    monkeypatch.setenv("FLAG", "0")
    assert env_bool("FLAG") is False


def test_env_bool_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("FLAG", raising=False)
    assert env_bool("FLAG", default=True) is True


def test_env_list_splits_and_trims(monkeypatch):
    monkeypatch.setenv("HOSTS", "a.example.com, b.example.com")
    assert env_list("HOSTS") == ["a.example.com", "b.example.com"]


def test_env_list_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("HOSTS", raising=False)
    assert env_list("HOSTS", default=["x"]) == ["x"]


def test_env_int_parses_integer(monkeypatch):
    monkeypatch.setenv("PORT", "5432")
    assert env_int("PORT") == 5432


def test_env_int_required_raises_when_missing(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    with pytest.raises(ImproperlyConfigured):
        env_int("PORT", required=True)
