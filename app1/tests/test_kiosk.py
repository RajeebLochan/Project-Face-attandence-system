"""Kiosk endpoints: states, limits, backpressure and confirmation tokens."""
from unittest import mock

from django.core import signing
from django.test import override_settings
from django.urls import reverse

from app1.models import Attendance, AuditEvent
from app1.views import kiosk as kiosk_views

from .fakes import FakeFaceEngine, one_face
from .helpers import BLUE, GREEN, RED, AppTestCase, image_bytes, make_student, upload


class KioskTestCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.staff = self.login(self.instructor())
        self.student = make_student(name="Kiosk Student", colour=RED)

    def recognize(self, colour=RED, mode="check_in", **extra):
        data = {"mode": mode, "frame": upload(image_bytes(colour)), **extra}
        return self.client.post(reverse("kiosk-recognize"), data)

    def confirm(self, token):
        return self.client.post(reverse("kiosk-confirm"), {"token": token})


class RecognizeStateTests(KioskTestCase):
    def test_kiosk_page_has_no_server_camera_controls(self):
        response = self.client.get(reverse("kiosk"))
        self.assertContains(response, "kiosk.js")
        self.assertContains(response, 'id="kiosk-camera"')

    def test_candidate_does_not_record_attendance_by_itself(self):
        response = self.recognize()
        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "candidate")
        self.assertEqual(data["candidate"]["registration_number"], self.student.registration_number)
        self.assertEqual(data["candidate"]["photo_url"], reverse("student-photo", args=[self.student.pk]))
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_unknown(self):
        self.assertEqual(self.recognize(colour=GREEN).json()["status"], "unknown")

    def test_ambiguous(self):
        make_student(colour=RED)
        self.assertEqual(self.recognize().json()["status"], "ambiguous")

    def test_unsuitable_frames(self):
        cases = {
            "no_face": lambda image: [],
            "multiple_faces": lambda image: one_face(image) * 2,
            "face_too_small": lambda image: one_face(image, size=10),
        }
        for reason, override in cases.items():
            with self.subTest(reason=reason):
                FakeFaceEngine.override = override
                data = self.recognize().json()
                self.assertEqual((data["status"], data["reason"]), ("unsuitable", reason))
                self.assertTrue(data["message"])

    @override_settings(FACE_ENGINE="app1.tests.fakes.UnavailableFaceEngine")
    def test_model_unavailable(self):
        response = self.recognize()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "model_unavailable")

    def test_invalid_requests(self):
        url = reverse("kiosk-recognize")
        self.assertEqual(self.client.post(url, {"mode": "check_in"}).json()["status"], "invalid_request")
        self.assertEqual(self.client.post(url, {"mode": "teleport", "frame": upload(image_bytes())}).status_code, 400)
        response = self.client.post(url, {"mode": "check_in", "frame": upload(b"not an image")})
        self.assertEqual((response.status_code, response.json()["status"]), (400, "invalid_frame"))

    @override_settings(KIOSK_FRAME_MAX_BYTES=10_000)
    def test_oversized_frame(self):
        response = self.client.post(reverse("kiosk-recognize"),
                                    {"mode": "check_in", "frame": upload(b"\xff\xd8" + b"0" * 20_000)})
        self.assertEqual((response.status_code, response.json()["status"]), (413, "frame_too_large"))

    def test_client_supplied_identity_is_ignored(self):
        other = make_student(colour=BLUE)
        data = self.recognize(colour=RED, student=other.pk, student_id=other.pk, distance="0").json()
        self.assertEqual(data["candidate"]["registration_number"], self.student.registration_number)

    def test_eligibility_rechecked_after_match(self):
        with mock.patch("app1.views.kiosk._eligible_students") as eligible:
            eligible.return_value.filter.return_value.first.return_value = None
            self.assertEqual(self.recognize().json()["status"], "unknown")


class BackpressureTests(KioskTestCase):
    @override_settings(KIOSK_MIN_FRAME_INTERVAL_SECONDS=30)
    def test_per_user_rate_limit(self):
        self.assertEqual(self.recognize().status_code, 200)
        response = self.recognize()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["status"], "throttled")
        self.assertIn("Retry-After", response)

    def test_busy_instead_of_queueing(self):
        slots = kiosk_views._analysis_slots()
        acquired = [slots.acquire(blocking=False) for _ in range(16)]
        try:
            response = self.recognize()
        finally:
            for ok in acquired:
                if ok:
                    slots.release()
        self.assertEqual((response.status_code, response.json()["status"]), (503, "busy"))
        self.assertEqual(FakeFaceEngine.calls, 0)

    def test_slot_released_after_errors(self):
        FakeFaceEngine.override = mock.Mock(side_effect=RuntimeError("inference crashed"))
        self.client.raise_request_exception = False
        self.assertEqual(self.recognize().status_code, 500)
        FakeFaceEngine.override = None
        self.assertEqual(self.recognize().json()["status"], "candidate")


class ConfirmTests(KioskTestCase):
    def candidate_token(self, mode="check_in"):
        return self.recognize(mode=mode).json()["candidate"]["token"]

    def test_confirm_records_once_and_audits_distance(self):
        token = self.candidate_token()
        response = self.confirm(token)
        self.assertEqual(response.json()["status"], "recorded")
        self.assertEqual(self.confirm(token).json()["status"], "already_recorded")
        self.assertEqual(self.confirm(self.candidate_token()).json()["status"], "already_recorded")
        self.assertEqual(Attendance.objects.count(), 1)
        event = AuditEvent.objects.get(action="check_in")
        self.assertEqual((event.source, event.actor, event.student), ("kiosk", self.staff, self.student))
        self.assertIn("match_distance", event.details)

    def test_check_out_flow(self):
        self.assertEqual(self.confirm(self.candidate_token("check_out")).json()["status"], "not_checked_in")
        self.confirm(self.candidate_token("check_in"))
        self.assertEqual(self.confirm(self.candidate_token("check_out")).json()["status"], "recorded")
        record = Attendance.objects.get()
        self.assertIsNotNone(record.check_out_time)

    def test_tampered_and_forged_tokens(self):
        token = self.candidate_token()
        self.assertEqual(self.confirm(token[:-2] + "xx").json()["status"], "invalid_token")
        forged = signing.dumps({"student": self.student.pk, "user": self.staff.pk, "mode": "check_in"},
                               salt="some-other-salt")
        self.assertEqual(self.confirm(forged).json()["status"], "invalid_token")
        self.assertEqual(self.confirm("").json()["status"], "invalid_token")
        self.assertEqual(Attendance.objects.count(), 0)

    def test_token_bound_to_user(self):
        token = self.candidate_token()
        self.login(self.instructor("teacher2"))
        self.assertEqual(self.confirm(token).json()["status"], "invalid_token")

    def test_expired_token(self):
        token = self.candidate_token()
        with override_settings(KIOSK_CANDIDATE_MAX_AGE_SECONDS=10), \
                mock.patch("django.core.signing.time.time", return_value=10**10):
            self.assertEqual(self.confirm(token).json()["status"], "expired")

    def test_revoked_between_match_and_confirm(self):
        token = self.candidate_token()
        type(self.student).objects.filter(pk=self.student.pk).update(authorized=False)
        response = self.confirm(token)
        self.assertEqual((response.status_code, response.json()["status"]), (409, "not_eligible"))
