"""
Settings shared by every environment.

Environment-specific modules build on this one:

* ``Project101.settings.dev``  - local development (default for ``manage.py``)
* ``Project101.settings.prod`` - production (default for WSGI/ASGI); refuses to start
  without a real secret key and explicit allowed hosts
* ``Project101.settings.test`` - automated tests (default for ``manage.py test``)

Configuration comes from environment variables. A local ``.env`` file is read if
present, but real environment variables always take precedence. See ``.env.example``.
"""
import math
import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _load_env_file(path):
    """Read simple KEY=VALUE lines from ``path`` without overriding the real environment."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_env_file(BASE_DIR / ".env")


def env(name, default=None):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_bool(name, default=False):
    value = env(name)
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"{name} must be a boolean (true/false), got {value!r}.")


def env_int(name, default, *, minimum=None, maximum=None):
    value = env(name)
    try:
        result = default if value is None else int(value)
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be an integer, got {value!r}.") from None
    if (minimum is not None and result < minimum) or (maximum is not None and result > maximum):
        raise ImproperlyConfigured(f"{name} must be between {minimum} and {maximum}, got {result}.")
    return result


def env_float(name, default, *, minimum, maximum, include_minimum=False):
    value = env(name)
    try:
        result = default if value is None else float(value)
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be a number, got {value!r}.") from None
    below = result < minimum if include_minimum else result <= minimum
    if not math.isfinite(result) or below or result >= maximum:
        bracket = "[" if include_minimum else "("
        raise ImproperlyConfigured(
            f"{name} must be a finite number in {bracket}{minimum}, {maximum}), got {value!r}."
        )
    return result


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


def env_path(name, default):
    path = Path(env(name, str(default))).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


def database_from_url(url):
    """Translate ``DATABASE_URL`` (``sqlite:///path`` or ``postgres://...``) into a Django DB dict."""
    if not url:
        url = "sqlite:///db.sqlite3"
    parts = urlsplit(url)
    if parts.scheme == "sqlite":
        name = unquote(parts.path[1:]) if parts.path.startswith("/") else unquote(parts.path)
        if not name:
            raise ImproperlyConfigured("DATABASE_URL for SQLite must include a file path.")
        path = Path(name)
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": path if path.is_absolute() else BASE_DIR / path,
            "OPTIONS": {
                # Wait for locks instead of failing immediately, and take the write lock at
                # the start of each transaction so concurrent writers serialise cleanly.
                "timeout": 20,
                "transaction_mode": "IMMEDIATE",
                "init_command": "PRAGMA journal_mode=WAL;",
            },
        }
    if parts.scheme in {"postgres", "postgresql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parts.path.lstrip("/")),
            "USER": unquote(parts.username or ""),
            "PASSWORD": unquote(parts.password or ""),
            "HOST": parts.hostname or "",
            "PORT": str(parts.port or ""),
            "OPTIONS": dict(parse_qsl(parts.query)),
            "CONN_MAX_AGE": env_int("DATABASE_CONN_MAX_AGE", 60, minimum=0),
            "CONN_HEALTH_CHECKS": True,
        }
    raise ImproperlyConfigured("DATABASE_URL must start with sqlite:// or postgres://.")


# --- Core ------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "app1.apps.App1Config",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "app1.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "Project101.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "app1.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "Project101.wsgi.application"

DATABASES = {"default": database_from_url(env("DATABASE_URL"))}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# --- Static files ----------------------------------------------------------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = env_path("DJANGO_STATIC_ROOT", BASE_DIR / "staticfiles")

# --- Private biometric storage ---------------------------------------------
# Enrolment photos are stored here and are NEVER exposed through a URL pattern or a
# web server alias. They are only returned by permission-checked views.
PRIVATE_MEDIA_ROOT = env_path("PRIVATE_MEDIA_ROOT", BASE_DIR / "var" / "private_media")
# Django's default storage is not used for user content; point it at the private root
# so that nothing is ever written to a publicly served directory by accident.
MEDIA_ROOT = PRIVATE_MEDIA_ROOT
MEDIA_URL = ""

# Upload limits (bytes). Files above FILE_UPLOAD_MAX_MEMORY_SIZE spill to a temp file;
# the views enforce their own, smaller per-purpose limits.
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700

# --- Authentication and sessions -------------------------------------------

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

SESSION_COOKIE_AGE = env_int("DJANGO_SESSION_COOKIE_AGE", 8 * 60 * 60, minimum=300)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# Login throttling (per username and per client address, see app1/throttle.py).
LOGIN_THROTTLE_MAX_FAILURES = env_int("LOGIN_THROTTLE_MAX_FAILURES", 5, minimum=1)
LOGIN_THROTTLE_MAX_FAILURES_PER_IP = env_int("LOGIN_THROTTLE_MAX_FAILURES_PER_IP", 50, minimum=1)
LOGIN_THROTTLE_WINDOW_SECONDS = env_int("LOGIN_THROTTLE_WINDOW_SECONDS", 15 * 60, minimum=60)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "attendance-default",
    }
}

# --- Face recognition ------------------------------------------------------

FACE_ENGINE = env("FACE_ENGINE", "app1.recognition.engine.OpenCVFaceEngine")
FACE_MODEL_DIR = env_path("FACE_MODEL_DIR", BASE_DIR / "var" / "models")
# Match rule: L2 distance between L2-normalised SFace embeddings (range 0..2).
# A DISTANCE IS NOT A CONFIDENCE OR PROBABILITY. The default 1.128 is the value
# published by OpenCV for SFace; it has NOT been calibrated for this deployment.
FACE_MATCH_THRESHOLD = env_float("FACE_MATCH_THRESHOLD", 1.128, minimum=0.0, maximum=2.0)
# The best student must beat the next-best *different* student by at least this much,
# otherwise the result is reported as ambiguous and nobody is proposed.
FACE_MATCH_MARGIN = env_float("FACE_MATCH_MARGIN", 0.10, minimum=0.0, maximum=2.0,
                              include_minimum=True)
FACE_DETECTION_SCORE = env_float("FACE_DETECTION_SCORE", 0.9, minimum=0.0, maximum=1.0)
FACE_MIN_SIZE_PX = env_int("FACE_MIN_SIZE_PX", 64, minimum=20, maximum=640)
FACE_ANALYSIS_MAX_SIDE = env_int("FACE_ANALYSIS_MAX_SIDE", 640, minimum=160, maximum=1920)

ENROLMENT_PHOTO_MAX_BYTES = env_int("ENROLMENT_PHOTO_MAX_BYTES", 5 * 1024 * 1024, minimum=10_000)
KIOSK_FRAME_MAX_BYTES = env_int("KIOSK_FRAME_MAX_BYTES", 1536 * 1024, minimum=10_000)
IMAGE_MIN_SIDE_PX = env_int("IMAGE_MIN_SIDE_PX", 160, minimum=64)
IMAGE_MAX_SIDE_PX = env_int("IMAGE_MAX_SIDE_PX", 4096, minimum=320)

# Kiosk backpressure: minimum seconds between frames per user, and how many frames
# the process analyses at once (extra frames get "busy" instead of queueing).
KIOSK_MIN_FRAME_INTERVAL_SECONDS = env_float("KIOSK_MIN_FRAME_INTERVAL_SECONDS", 0.5,
                                             minimum=0.0, maximum=60.0, include_minimum=True)
KIOSK_MAX_CONCURRENT_ANALYSES = env_int("KIOSK_MAX_CONCURRENT_ANALYSES", 1, minimum=1, maximum=16)
KIOSK_CANDIDATE_MAX_AGE_SECONDS = env_int("KIOSK_CANDIDATE_MAX_AGE_SECONDS", 120, minimum=10)

# --- E-mail ---------------------------------------------------------------
# Attendance e-mails are written to an outbox table and sent by a separate worker
# (`python manage.py run_notification_worker`). Credentials only come from the environment.
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587, minimum=1, maximum=65535)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured("Set only one of EMAIL_USE_TLS (STARTTLS, port 587) or EMAIL_USE_SSL (port 465).")
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 15, minimum=1, maximum=120)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "")
SERVER_EMAIL = env("SERVER_EMAIL", DEFAULT_FROM_EMAIL or "root@localhost")
EMAIL_SUBJECT_PREFIX = "[Attendance] "
# Public base URL used for links in e-mails (verification links, staff links to records).
SITE_URL = env("SITE_URL", "").rstrip("/")

NOTIFICATION_MAX_ATTEMPTS = env_int("NOTIFICATION_MAX_ATTEMPTS", 5, minimum=1, maximum=20)
# A job left in "processing" longer than this (worker crashed) is requeued.
NOTIFICATION_LEASE_SECONDS = env_int("NOTIFICATION_LEASE_SECONDS", 300, minimum=30)
NOTIFICATION_WORKER_POLL_SECONDS = env_int("NOTIFICATION_WORKER_POLL_SECONDS", 5, minimum=1, maximum=300)
TEST_EMAIL_INTERVAL_SECONDS = env_int("TEST_EMAIL_INTERVAL_SECONDS", 60, minimum=1)
# Manual entries this many minutes before "now" are treated as past (backdated) entries.
BACKDATED_ENTRY_MINUTES = env_int("BACKDATED_ENTRY_MINUTES", 15, minimum=1)

# Version of the institution's privacy notice that staff confirm consent against.
PRIVACY_NOTICE_VERSION = env("PRIVACY_NOTICE_VERSION", "1.0")

# --- Logging ---------------------------------------------------------------
# Never log images, embeddings, names or e-mail addresses; log primary keys only.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
