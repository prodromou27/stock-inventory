from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .env import env_bool, env_int, env_list, env_str

# Hardcoded, not environment-controlled: production must never run with DEBUG=True
# even if an operator misconfigures the DEBUG environment variable.
DEBUG = False

SECRET_KEY = env_str("SECRET_KEY", required=True)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", default=[])
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set in production")

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", default=[])

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", default=60 * 60 * 8)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", default=3600)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# Requires `manage.py collectstatic` to have run during image build/deploy —
# see docs/architecture/08-nonfunctional-plan.md and the Phase 8 deployment runbook.
STORAGES = {  # noqa: F405
    **STORAGES,  # noqa: F405
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

LOGGING["handlers"]["console"]["formatter"] = "json"  # noqa: F405
