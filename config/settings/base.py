from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

from .env import env_bool, env_int, env_list, env_str

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Loads a local .env file if present (Docker Compose injects env vars directly and
# does not need this, but `manage.py`/pytest run outside Docker do).
load_dotenv(BASE_DIR / ".env", override=False)

SECRET_KEY = env_str("SECRET_KEY", default="dev-insecure-secret-key-change-me")
DEBUG = env_bool("DEBUG", default=False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "axes",
    "apps.core",
    "apps.audit",
    "apps.locations",
    "apps.accounts",
    "apps.catalog",
    "apps.inventory",
    "apps.documents",
    "apps.reporting",
    "apps.imports",
    "apps.exports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Must be last — inspects the response of every request, including ones
    # AxesStandaloneBackend already blocked (doc 08's login throttling).
    "axes.middleware.AxesMiddleware",
]

AUTHENTICATION_BACKENDS = [
    # Must be first — checks the lockout state before Django's own backend
    # ever compares a password (docs/architecture/08-nonfunctional-plan.md).
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.role_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env_str("POSTGRES_DB", default="stock_inventory"),
        "USER": env_str("POSTGRES_USER", default="stock_inventory"),
        "PASSWORD": env_str("POSTGRES_PASSWORD", default="stock_inventory"),
        "HOST": env_str("POSTGRES_HOST", default="localhost"),
        "PORT": env_str("POSTGRES_PORT", default="5432"),
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", default=60),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env_str("TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Plain (non-manifest) static storage by default so templates render without
# requiring `collectstatic` to have run first (dev/test). Production overrides
# this to the compressed-manifest backend after collectstatic runs at deploy time.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Protected volume for future attachments/generated documents (Phase 5). Never
# served directly via MEDIA_URL — downloads always go through an authorized view.
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "login"

# Login throttling (docs/architecture/08-nonfunctional-plan.md). Locked out by
# the (username, IP) combination, not IP alone — a shared office/VPN egress IP
# must not lock out every user behind it, and an attacker must not be able to
# lock a known username out from every IP just by guessing its password
# repeatedly from one machine. Exact numbers are a deferred/defaulted decision
# (spec §24), overridable via env before launch, same as the password policy.
AXES_FAILURE_LIMIT = env_int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = timedelta(minutes=env_int("AXES_COOLOFF_MINUTES", default=30))
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
        "json": {"()": "apps.core.logging.JSONFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": env_str("LOG_FORMATTER", default="console"),
        },
    },
    "root": {"handlers": ["console"], "level": env_str("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": env_str("DJANGO_LOG_LEVEL", default="INFO"),
            "propagate": False,
        },
        # WeasyPrint (and fontTools, which it uses for font subsetting)
        # log per-step progress noisily at INFO on every PDF render;
        # warnings (e.g. unsupported CSS) still come through.
        "weasyprint": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "fontTools": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
