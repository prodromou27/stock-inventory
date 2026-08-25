import tempfile
from pathlib import Path

from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "test-secret-key-not-for-production"
ALLOWED_HOSTS = ["testserver", "localhost"]

# Outside the repo tree by default so a test that forgets to override this
# (tests/test_settings_services.py's certificate tests use pytest-django's
# `settings` fixture to point it at a real tmp_path) can't accidentally
# write into the real deploy/certs/ during a local test run.
CERTS_DIR = str(Path(tempfile.gettempdir()) / "stock_inventory_test_certs")

# Fast (insecure) hasher — this settings module is test-only.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
