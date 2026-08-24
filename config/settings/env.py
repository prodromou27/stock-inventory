"""Small environment-variable helpers shared by every settings module.

Kept dependency-free (stdlib only) and side-effect-free (no reading of a
specific .env file here — that happens once, in base.py) so it can be
imported and unit tested in isolation.
"""

import os

from django.core.exceptions import ImproperlyConfigured


def env_str(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise ImproperlyConfigured(f"Required environment variable {name} is not set")
    return value


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=None, separator=","):
    value = os.environ.get(name)
    if not value:
        return list(default) if default else []
    return [item.strip() for item in value.split(separator) if item.strip()]


def env_int(name, default=None, required=False):
    value = os.environ.get(name)
    if value is None or value == "":
        if required:
            raise ImproperlyConfigured(f"Required environment variable {name} is not set")
        return default
    return int(value)
