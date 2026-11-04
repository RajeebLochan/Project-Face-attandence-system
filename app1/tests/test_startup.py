"""Ordinary start-up must not download models, initialise inference or touch cameras."""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

PROJECT = Path(settings.BASE_DIR)

PROBE = r"""
import socket, sys
def _deny(*args, **kwargs):
    raise AssertionError("network access attempted during start-up")
socket.socket.connect = _deny
socket.create_connection = _deny

import django
django.setup()
from django.core.management import call_command
from django.urls import get_resolver
call_command("check", verbosity=0)
call_command("migrate", verbosity=0)
call_command("makemigrations", "--check", "--dry-run", verbosity=0)
import Project101.urls, app1.admin
for module in ("auth", "dashboard", "students", "attendance", "kiosk", "errors"):
    __import__("app1.views." + module)
get_resolver().url_patterns
from app1.recognition import engine
assert engine._engine is None, "face engine was created at start-up"
assert "cv2" not in sys.modules, "OpenCV was imported at start-up"
import os
from django.conf import settings
assert not os.path.exists(settings.FACE_MODEL_DIR), "model directory was touched"
print("START-UP-OK")
"""


class StartupSideEffectTests(SimpleTestCase):
    def test_check_and_migrate_do_not_load_models_network_or_opencv(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                **os.environ,
                "DJANGO_SETTINGS_MODULE": "Project101.settings.dev",
                "DATABASE_URL": f"sqlite:///{tmp}/startup.sqlite3",
                "FACE_MODEL_DIR": f"{tmp}/models",
                "PRIVATE_MEDIA_ROOT": f"{tmp}/private",
                "FACE_ENGINE": "app1.recognition.engine.OpenCVFaceEngine",
            }
            result = subprocess.run(
                [sys.executable, "-c", PROBE], cwd=PROJECT, env=env,
                capture_output=True, text=True, timeout=180,
            )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn("START-UP-OK", result.stdout)

    def test_application_code_has_no_server_camera_audio_or_window_calls(self):
        forbidden = re.compile(r"VideoCapture|imshow|namedWindow|waitKey|pygame|destroyWindow")
        offenders = []
        for path in list((PROJECT / "app1").rglob("*.py")) + list((PROJECT / "templates").rglob("*.html")):
            if "tests" in path.parts:
                continue
            if forbidden.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(PROJECT)))
        self.assertEqual(offenders, [])
