"""Production settings must fail closed; representative production settings must pass --deploy."""
import os
import subprocess
import sys
import tempfile

from django.conf import settings
from django.test import SimpleTestCase

STRONG_KEY = "k" * 20 + "Zq8#xP2!vR7@mN4$wL1%tY6^bH3&jC9*" + "e" * 10


def run(code, env, timeout=120):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("DJANGO_")}
    environment.update(env)
    return subprocess.run([sys.executable, *code], cwd=settings.BASE_DIR, env=environment,
                          capture_output=True, text=True, timeout=timeout)


def prod_setup(**env):
    return run(["-c", "import django; django.setup()"], {"DJANGO_SETTINGS_MODULE": "Project101.settings.prod", **env})


class ProductionSettingsTests(SimpleTestCase):
    def test_missing_secret_key(self):
        result = prod_setup(DJANGO_ALLOWED_HOSTS="attendance.example.edu")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY must be set", result.stderr)

    def test_weak_secret_key(self):
        for key in ("django-insecure-" + "x" * 60, "short", "a" * 60):
            with self.subTest(key=key[:20]):
                result = prod_setup(DJANGO_SECRET_KEY=key, DJANGO_ALLOWED_HOSTS="attendance.example.edu")
                self.assertIn("too weak", result.stderr)

    def test_missing_or_wildcard_hosts(self):
        self.assertIn("DJANGO_ALLOWED_HOSTS must list", prod_setup(DJANGO_SECRET_KEY=STRONG_KEY).stderr)
        self.assertIn("must not contain '*'",
                      prod_setup(DJANGO_SECRET_KEY=STRONG_KEY, DJANGO_ALLOWED_HOSTS="*").stderr)

    def test_invalid_numeric_configuration(self):
        for value in ("nan", "inf", "-1", "2.5", "abc"):
            with self.subTest(value=value):
                result = prod_setup(DJANGO_SECRET_KEY=STRONG_KEY, DJANGO_ALLOWED_HOSTS="a.example",
                                    FACE_MATCH_THRESHOLD=value)
                self.assertIn("FACE_MATCH_THRESHOLD must be", result.stderr)

    def test_representative_production_settings_pass_deploy_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(
                ["manage.py", "check", "--deploy", "--fail-level", "WARNING"],
                {
                    "DJANGO_SETTINGS_MODULE": "Project101.settings.prod",
                    "DJANGO_SECRET_KEY": STRONG_KEY,
                    "DJANGO_ALLOWED_HOSTS": "attendance.example.edu",
                    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://attendance.example.edu",
                    "DJANGO_TRUST_X_FORWARDED_PROTO": "true",
                    "DJANGO_SECURE_HSTS_PRELOAD": "true",
                    "DATABASE_URL": f"sqlite:///{tmp}/prod.sqlite3",
                    "DJANGO_CACHE_DIR": f"{tmp}/cache",
                },
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no issues", result.stdout)

    def test_production_has_debug_off_and_secure_cookies(self):
        code = ("import django; django.setup(); from django.conf import settings as s; "
                "print(s.DEBUG, s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, s.SECURE_SSL_REDIRECT, "
                "s.SECURE_HSTS_SECONDS > 0, s.SECURE_PROXY_SSL_HEADER is not None)")
        result = run(["-c", code], {"DJANGO_SETTINGS_MODULE": "Project101.settings.prod",
                                    "DJANGO_SECRET_KEY": STRONG_KEY, "DJANGO_ALLOWED_HOSTS": "a.example"})
        self.assertEqual(result.stdout.split(), ["False", "True", "True", "True", "True", "False"])
