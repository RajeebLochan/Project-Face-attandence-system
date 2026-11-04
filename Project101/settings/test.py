"""Settings for the automated test-suite: isolated database, private media and models."""
import tempfile
from pathlib import Path

from .dev import *  # noqa: F403
from .dev import DATABASES

DEBUG = False
SECRET_KEY = "django-insecure-test-only"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

_TMP = Path(tempfile.mkdtemp(prefix="attendance-tests-"))
PRIVATE_MEDIA_ROOT = _TMP / "private_media"
MEDIA_ROOT = PRIVATE_MEDIA_ROOT
# Empty model directory: tests use a fake engine unless real models are provided
# explicitly through FACE_TEST_MODEL_DIR (see app1/tests/test_real_model.py).
FACE_MODEL_DIR = _TMP / "models"
FACE_ENGINE = "app1.tests.fakes.FakeFaceEngine"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
KIOSK_MIN_FRAME_INTERVAL_SECONDS = 0.0

if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    # A file-based test database (not in-memory) so that multi-threaded tests use the
    # same locking behaviour as a real SQLite deployment.
    DATABASES["default"]["TEST"] = {"NAME": str(_TMP / "test.sqlite3")}

LOGGING = {"version": 1, "disable_existing_loggers": False,
           "handlers": {"null": {"class": "logging.NullHandler"}},
           "root": {"handlers": ["null"], "level": "CRITICAL"}}
