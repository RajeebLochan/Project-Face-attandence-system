"""Browser tests for the kiosk and responsive layout (Playwright + Chromium fake camera).

Tagged "browser"; skipped automatically when Playwright or a Chromium build is not
installed. Uses Chromium's synthetic camera and the fake face engine, so these tests
check the browser workflow, NOT recognition accuracy or real camera hardware.
"""
import os
import unittest

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings, tag

from app1.models import Attendance
from app1.recognition.engine import ModelUnavailable, reset_engine
from app1.recognition.gallery import gallery

from .fakes import FakeFaceEngine, one_face
from .helpers import GREEN, RED, make_student, make_user, solid

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover - optional dev dependency
    sync_playwright = None

FAKE_CAMERA_ARGS = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
DENY_CAMERA_SCRIPT = """
navigator.mediaDevices.getUserMedia = () =>
  Promise.reject(new DOMException("denied by test", "NotAllowedError"));
"""


@tag("browser")
@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
@override_settings(KIOSK_MIN_FRAME_INTERVAL_SECONDS=0.0)
class KioskBrowserTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        # Playwright's sync API runs an event loop in this thread; the ORM calls made by
        # the test itself are synchronous and safe.
        os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        try:
            executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
            cls.browser = cls.playwright.chromium.launch(args=FAKE_CAMERA_ARGS, executable_path=executable)
        except Exception as exc:  # noqa: BLE001
            cls.playwright.stop()
            super().tearDownClass()
            raise unittest.SkipTest(f"Chromium is not available: {exc}") from None

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        FakeFaceEngine.reset()
        reset_engine()
        gallery.invalidate()
        self.student = make_student(name="Browser Student", colour=RED)
        user = make_user("kiosk-browser", group="Instructor")
        self.client.force_login(user)
        self.context = self.browser.new_context(base_url=self.live_server_url)
        self.context.add_cookies([{
            "name": settings.SESSION_COOKIE_NAME,
            "value": self.client.cookies[settings.SESSION_COOKIE_NAME].value,
            "url": self.live_server_url,
        }])
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)

    def tearDown(self):
        self.context.close()

    def open_kiosk(self):
        self.page.goto("/kiosk/")
        expect(self.page.locator("#kiosk-status")).to_contain_text("Press Start")

    def test_candidate_confirm_and_stop_releases_camera(self):
        # The fake camera shows a test pattern; make the fake engine "see" the student.
        FakeFaceEngine.override = lambda image: one_face(solid(RED))
        self.open_kiosk()
        self.page.click("#kiosk-start")
        expect(self.page.locator("#kiosk-candidate")).to_be_visible()
        expect(self.page.locator("#candidate-name")).to_have_text("Browser Student")
        expect(self.page.locator("#candidate-photo")).to_be_visible()
        # Scanning pauses while a candidate is shown (no frames accumulate).
        sent = self.page.evaluate("window.__kiosk.framesSent")
        self.page.wait_for_timeout(1000)
        self.assertEqual(self.page.evaluate("window.__kiosk.framesSent"), sent)
        self.assertEqual(Attendance.objects.count(), 0)

        self.page.click("#candidate-confirm")
        expect(self.page.locator("#kiosk-status")).to_contain_text("check-in recorded")
        self.assertEqual(Attendance.objects.filter(student=self.student).count(), 1)

        self.page.click("#kiosk-stop")
        expect(self.page.locator("#kiosk-status")).to_contain_text("camera is off")
        released = self.page.evaluate("""() => {
            const video = document.getElementById('kiosk-video');
            return window.__kiosk.state.stream === null && video.srcObject === null;
        }""")
        self.assertTrue(released)

    def test_reject_records_nothing(self):
        FakeFaceEngine.override = lambda image: one_face(solid(RED))
        self.open_kiosk()
        self.page.click("#kiosk-start")
        expect(self.page.locator("#kiosk-candidate")).to_be_visible()
        self.page.click("#candidate-reject")
        expect(self.page.locator("#kiosk-status")).to_contain_text("Nothing was recorded")
        self.page.click("#kiosk-stop")
        self.assertEqual(Attendance.objects.count(), 0)

    def test_unknown_and_unsuitable_states(self):
        FakeFaceEngine.override = lambda image: one_face(solid(GREEN))
        self.open_kiosk()
        self.page.click("#kiosk-start")
        expect(self.page.locator("#kiosk-status")).to_contain_text("Not recognised")
        FakeFaceEngine.override = lambda image: []
        expect(self.page.locator("#kiosk-status")).to_contain_text("No face was found")
        self.page.click("#kiosk-stop")

    def test_model_unavailable_stops_scanning(self):
        def unavailable(image):
            raise ModelUnavailable("not installed")

        FakeFaceEngine.override = unavailable
        self.open_kiosk()
        self.page.click("#kiosk-start")
        expect(self.page.locator("#kiosk-status")).to_contain_text("not available on this server")
        expect(self.page.locator("#kiosk-start")).to_be_enabled()
        self.assertTrue(self.page.evaluate("window.__kiosk.state.stream === null"))

    def test_camera_permission_denied(self):
        self.page.add_init_script(DENY_CAMERA_SCRIPT)
        self.open_kiosk()
        self.page.click("#kiosk-start")
        expect(self.page.locator("#kiosk-status")).to_contain_text("permission was denied")

    def test_leaving_the_page_releases_the_camera(self):
        FakeFaceEngine.override = lambda image: []
        self.open_kiosk()
        self.page.click("#kiosk-start")
        expect(self.page.locator("#kiosk-stop")).to_be_enabled()
        self.page.evaluate("""() => {
            window.__tracks = window.__kiosk.state.stream.getTracks();
            window.dispatchEvent(new PageTransitionEvent('pagehide'));
        }""")
        states = self.page.evaluate("window.__tracks.map(t => t.readyState)")
        self.assertTrue(states and all(state == "ended" for state in states))

    def test_no_horizontal_overflow_on_phone_width(self):
        self.page.set_viewport_size({"width": 360, "height": 740})
        for i in range(30):
            Attendance.objects.create(student=make_student(name=f"Long Student Name Number {i}", embedding=False),
                                      date="2025-01-01")
        for path in ("/", "/kiosk/", "/students/", "/students/attendance/", f"/students/{self.student.pk}/"):
            with self.subTest(path=path):
                self.page.goto(path)
                overflow = self.page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                self.assertLessEqual(overflow, 0)

    def test_pages_have_labels_and_no_csp_violations(self):
        violations = []
        def collect(message):
            if "Content Security Policy" in message.text:
                violations.append(message.text)

        self.page.on("console", collect)
        for path in ("/", "/kiosk/", "/students/", "/students/attendance/", "/attendance/manual/"):
            self.page.goto(path)
            unlabeled = self.page.evaluate("""() => [...document.querySelectorAll('input, select, textarea')]
                .filter(el => el.type !== 'hidden' && !el.labels?.length && !el.closest('label')
                              && !el.getAttribute('aria-label'))
                .map(el => el.name || el.id)""")
            self.assertEqual(unlabeled, [], path)
        self.assertEqual(violations, [])
