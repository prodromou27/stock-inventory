from .base import *  # noqa: F401,F403
from .env import env_str

DEBUG = True
ALLOWED_HOSTS = ["*"]
SECRET_KEY = env_str("SECRET_KEY", default="dev-insecure-secret-key-change-me")
INTERNAL_IPS = ["127.0.0.1"]
