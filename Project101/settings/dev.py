"""Local development settings. Never use these on a server reachable by others."""
from .base import *  # noqa: F403
from .base import env, env_bool, env_list

DEBUG = env_bool("DJANGO_DEBUG", True)

# A development-only key. Production refuses any key with this prefix.
SECRET_KEY = env("DJANGO_SECRET_KEY", "django-insecure-dev-only-do-not-use-in-production")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
