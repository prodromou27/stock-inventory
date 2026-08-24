from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "test-secret-key-not-for-production"
ALLOWED_HOSTS = ["testserver", "localhost"]

# Fast (insecure) hasher — this settings module is test-only.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
