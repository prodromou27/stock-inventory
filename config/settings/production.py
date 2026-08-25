from .base import *  # noqa: F401,F403
from .env import env_bool, env_int, env_list, env_str

# Hardcoded, not environment-controlled: production must never run with DEBUG=True
# even if an operator misconfigures the DEBUG environment variable.
DEBUG = False

SECRET_KEY = env_str("SECRET_KEY", required=True)

# Defaults to "accept any host" (Django's own wildcard) rather than failing closed —
# streamlined-install request: a fresh single-command deployment shouldn't need a
# hostname decided up front. Tighten this in .env.production (a comma-separated list)
# once you know the real hostname(s); nothing else in the app depends on it being
# wildcarded, and doing so does not disable HTTPS, CSRF, or session cookie security.
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", default=["*"])

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", default=[])

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", default=60 * 60 * 8)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# The Docker healthcheck (deploy/docker-compose.prod.yml) curls `web` directly
# on its plain-HTTP port, entirely bypassing `proxy`'s TLS termination, so it
# can never present X-Forwarded-Proto. Without this exemption,
# SECURE_SSL_REDIRECT sends it a redirect to an https:// URL on a port that
# never speaks TLS, and the healthcheck hangs until the handshake times out —
# `web` is unhealthy forever even though the app itself is fine. `web` is
# never reachable from outside the Docker network (only `proxy` is published
# to the host), so exempting this one path from the redirect exposes nothing.
SECURE_REDIRECT_EXEMPT = [r"^healthz/?$"]
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", default=3600)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# Off by default: submitting to a browser's HSTS preload list is effectively
# one-way (removal takes months to propagate once accepted), so it's an
# explicit operator opt-in via env, not a framework default — see
# deploy/DEPLOYMENT.md.
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", default=False)

# Requires `manage.py collectstatic` to have run during image build/deploy —
# see docs/architecture/08-nonfunctional-plan.md and the Phase 8 deployment runbook.
STORAGES = {  # noqa: F405
    **STORAGES,  # noqa: F405
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

LOGGING["handlers"]["console"]["formatter"] = "json"  # noqa: F405

# The app connects at runtime as a separate, lower-privilege role than the one
# that ran migrations, when RUNTIME_DB_USER is set (deploy/sql/hardening_runtime_role.sql,
# deploy/DEPLOYMENT.md) — defense in depth so a bug in application code cannot UPDATE/DELETE
# an audit/ledger row, since the migration-owning role can bypass GRANT/REVOKE on tables it
# owns. Falls back to POSTGRES_USER/PASSWORD (the owning role) so production still runs, just
# without this extra hardening layer, if the runtime role hasn't been provisioned yet.
_runtime_db_user = env_str("RUNTIME_DB_USER", default="")
if _runtime_db_user:
    DATABASES["default"]["USER"] = _runtime_db_user  # noqa: F405
    DATABASES["default"]["PASSWORD"] = env_str("RUNTIME_DB_PASSWORD", required=True)  # noqa: F405
