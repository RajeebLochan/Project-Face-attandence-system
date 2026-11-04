"""Local development settings. Never use these on a server reachable by others."""
from .base import *  # noqa: F403
from .base import BASE_DIR, env, env_bool, env_list

DEBUG = env_bool("DJANGO_DEBUG", True)

# A development-only key. Production refuses any key with this prefix.
SECRET_KEY = env("DJANGO_SECRET_KEY", "django-insecure-dev-only-do-not-use-in-production")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")

# Development never talks to a real mail server unless EMAIL_BACKEND is set explicitly:
# messages are written as files under var/sent-mail (or use Mailpit, see docs/NOTIFICATIONS.md).
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.filebased.EmailBackend")
EMAIL_FILE_PATH = env("EMAIL_FILE_PATH", str(BASE_DIR / "var" / "sent-mail"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "attendance@localhost")
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")
