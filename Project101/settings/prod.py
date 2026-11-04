"""
Production settings.

Start-up fails with ImproperlyConfigured unless DJANGO_SECRET_KEY and
DJANGO_ALLOWED_HOSTS are set to real values.

Reverse-proxy assumptions (see README "Deployment"):

* TLS terminates at a reverse proxy (for example nginx) in front of gunicorn.
* Only if that proxy *overwrites* X-Forwarded-Proto on every request should
  DJANGO_TRUST_X_FORWARDED_PROTO be set to true; otherwise clients could spoof HTTPS.
* The proxy serves STATIC_ROOT at /static/ and must NOT serve PRIVATE_MEDIA_ROOT.
"""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import BASE_DIR, env, env_bool, env_int, env_list, env_path

DEBUG = False

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in production.")
if SECRET_KEY.startswith("django-insecure") or len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is too weak: use at least 50 random characters "
        "(python -c \"from django.core.management.utils import get_random_secret_key as k; print(k())\")."
    )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list the host names this site serves.")
if "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must not contain '*' in production.")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

if env_bool("DJANGO_TRUST_X_FORWARDED_PROTO", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 31536000, minimum=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)

# Shared on-disk cache so login throttling and kiosk rate limits apply across all
# worker processes on one host. Use a shared cache (database/Redis) for multiple hosts.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": str(env_path("DJANGO_CACHE_DIR", BASE_DIR / "var" / "cache")),
    }
}

ADMINS = [("Administrator", addr) for addr in env_list("DJANGO_ADMIN_EMAILS")]
