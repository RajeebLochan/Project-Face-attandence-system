"""Enrolment validation, consent, authorisation and biometric clean-up."""
from django.test import override_settings
from django.urls import reverse

from app1.models import AuditEvent, FaceEmbedding, Student
from app1.storage import PrivateMediaStorage

from .fakes import FakeFaceEngine, one_face
from .helpers import BLUE, RED, AppTestCase, image_bytes, make_student, upload


def enrolment_data(**overrides):
    data = {
        "name": "  Synthetic   Student ",
        "email": "Synthetic@Example.TEST",
        "registration_number": " ab-123 ",
        "branch": "CSE",
        "year": "2nd Year",
        "consent": "on",
        "photo": upload(image_bytes(RED)),
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


class EnrolmentTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.login(self.admin_user())
        self.url = reverse("student-enroll")

    def test_valid_enrolment_normalises_and_stores_privately(self):
        response = self.client.post(self.url, enrolment_data())
        student = Student.objects.get()
        self.assertRedirects(response, reverse("student-detail", args=[student.pk]))
        self.assertEqual(student.name, "Synthetic Student")
        self.assertEqual(student.email, "synthetic@example.test")
        self.assertEqual(student.registration_number, "AB-123")
        self.assertFalse(student.authorized)
        self.assertEqual(student.consent_status, Student.Consent.GIVEN)
        self.assertEqual(student.consent_notice_version, "1.0")
        self.assertEqual(student.consent_recorded_by, self.admin)
        self.assertTrue(PrivateMediaStorage().exists(student.image.name))
        self.assertEqual(FaceEmbedding.objects.filter(student=student).count(), 1)
        self.assertTrue(AuditEvent.objects.filter(action="enrolled", student=student, actor=self.admin).exists())

    def assert_rejected(self, data, field, fragment):
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Student.objects.count(), 0)
        form = response.context["form"]
        errors = form.errors.get(field, []) if field else form.non_field_errors()
        self.assertTrue(any(fragment in str(error) for error in errors), errors)

    def test_consent_is_required(self):
        self.assert_rejected(enrolment_data(consent=None), "consent", "Consent must be recorded")

    def test_field_validation(self):
        cases = {
            "email": ("not-an-email", "valid email"),
            "registration_number": ("!!", "Use 3–15 characters"),
            "branch": ("XYZ", "Select a valid choice"),
            "year": ("9th Year", "Select a valid choice"),
            "name": ("x", "full name"),
        }
        for field, (value, fragment) in cases.items():
            with self.subTest(field=field):
                self.assert_rejected(enrolment_data(**{field: value}), field, fragment)

    def test_long_values_are_rejected_not_truncated(self):
        self.assert_rejected(enrolment_data(name="x" * 300), "name", "at most 255")
        self.assert_rejected(enrolment_data(registration_number="A" * 16), "registration_number", "at most 15")

    def test_duplicate_registration_number_is_case_insensitive(self):
        make_student(registration_number="AB-123")
        self.assertEqual(Student.objects.count(), 1)
        response = self.client.post(self.url, enrolment_data(registration_number="ab-123"))
        self.assertContains(response, "already exists")
        self.assertEqual(Student.objects.count(), 1)

    def test_resubmitting_the_same_form_does_not_create_a_duplicate(self):
        self.client.post(self.url, enrolment_data())
        response = self.client.post(self.url, enrolment_data())
        self.assertContains(response, "already exists")
        self.assertEqual(Student.objects.count(), 1)

    def test_students_may_share_a_name(self):
        self.client.post(self.url, enrolment_data(name="Same Name", registration_number="R-001"))
        self.client.post(self.url, enrolment_data(name="Same Name", registration_number="R-002",
                                                   photo=upload(image_bytes(BLUE))))
        self.assertEqual(Student.objects.filter(name="Same Name").count(), 2)

    def test_invalid_images(self):
        png = image_bytes(RED, fmt="PNG")
        gif = image_bytes(RED, fmt="GIF")
        jpeg = image_bytes(RED)
        cases = {
            "missing": (None, "required"),
            "empty": (upload(b""), "empty"),
            "not an image": (upload(b"<?php echo 1; ?>"), "could not be read"),
            "truncated": (upload(jpeg[: len(jpeg) // 2]), "could not be read"),
            "gif": (upload(gif, "photo.gif", "image/gif"), "Only JPEG and PNG"),
            "too small": (upload(image_bytes(RED, size=(100, 100))), "at least"),
            "too large dims": (upload(image_bytes(RED, size=(5000, 200))), "at most"),
            "content checked, not extension": (upload(b"GIF89a....", "photo.png", "image/png"), "could not be read"),
        }
        for label, (photo, fragment) in cases.items():
            with self.subTest(case=label):
                self.assert_rejected(enrolment_data(photo=photo), "photo", fragment)
        # A PNG is accepted and re-encoded as JPEG.
        self.client.post(self.url, enrolment_data(photo=upload(png, "photo.png", "image/png")))
        student = Student.objects.get()
        with PrivateMediaStorage().open(student.image.name) as handle:
            self.assertTrue(handle.read(3) == b"\xff\xd8\xff")

    @override_settings(ENROLMENT_PHOTO_MAX_BYTES=20_000)
    def test_byte_limit(self):
        big = upload(b"\xff\xd8\xff" + b"0" * 30_000)
        self.assert_rejected(enrolment_data(photo=big), "photo", "larger than")

    def test_zero_faces(self):
        FakeFaceEngine.override = lambda image: []
        self.assert_rejected(enrolment_data(), "photo", "No face")

    def test_multiple_faces(self):
        FakeFaceEngine.override = lambda image: one_face(image) * 2
        self.assert_rejected(enrolment_data(), "photo", "More than one face")

    def test_face_too_small(self):
        FakeFaceEngine.override = lambda image: one_face(image, size=20)
        self.assert_rejected(enrolment_data(), "photo", "too small")

    @override_settings(FACE_ENGINE="app1.tests.fakes.UnavailableFaceEngine")
    def test_model_unavailable_is_a_clear_error(self):
        self.assert_rejected(enrolment_data(), None, "models are not installed")

    def test_failed_enrolment_leaves_no_file(self):
        FakeFaceEngine.override = lambda image: []
        self.client.post(self.url, enrolment_data())
        self.assertEqual([p for p in self.media_root.rglob("*") if p.is_file()], [])

    def test_database_failure_after_file_write_removes_the_new_file(self):
        from unittest import mock

        with mock.patch("app1.services.students.AuditEvent.objects.create", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, enrolment_data())
        self.assertEqual(Student.objects.count(), 0)
        self.assertEqual([p for p in self.media_root.rglob("*") if p.is_file()], [])


class AuthorizationTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.login(self.admin_user())

    def post(self, student, decision):
        return self.client.post(reverse("student-authorize", args=[student.pk]), {"decision": decision})

    def test_authorize_page_shows_protected_photo(self):
        student = make_student(authorized=False)
        response = self.client.get(reverse("student-authorize", args=[student.pk]))
        self.assertContains(response, reverse("student-photo", args=[student.pk]))

    def test_strict_decision_values(self):
        student = make_student(authorized=False)
        for value in ("false", "true", "1", "on", "", "yes"):
            with self.subTest(value=value):
                response = self.post(student, value)
                self.assertEqual(response.status_code, 200)
                student.refresh_from_db()
                self.assertFalse(student.authorized)

    def test_authorize_and_revoke_are_audited(self):
        student = make_student(authorized=False)
        self.post(student, "authorize")
        student.refresh_from_db()
        self.assertTrue(student.authorized)
        self.assertEqual(student.authorized_by, self.admin)
        self.post(student, "revoke")
        student.refresh_from_db()
        self.assertFalse(student.authorized)
        actions = list(AuditEvent.objects.filter(student=student).values_list("action", flat=True))
        self.assertIn("authorized", actions)
        self.assertIn("authorization_revoked", actions)

    def test_cannot_authorize_without_consent_photo_or_template(self):
        cases = {
            "no consent": make_student(authorized=False, consent=False),
            "no photo": make_student(authorized=False, photo=False, embedding=False),
            "no template": make_student(authorized=False, embedding=False),
            "stale template": make_student(authorized=False, model_version="old-model"),
        }
        for label, student in cases.items():
            with self.subTest(case=label):
                response = self.post(student, "authorize")
                self.assertEqual(response.status_code, 200)
                student.refresh_from_db()
                self.assertFalse(student.authorized)


class BiometricCleanupTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.login(self.admin_user())
        self.storage = PrivateMediaStorage()

    def test_replacing_photo_deletes_old_file_after_commit_and_requires_reauthorization(self):
        student = make_student()
        old_name = student.image.name
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("student-photo-replace", args=[student.pk]),
                {"photo": upload(image_bytes(BLUE)), "consent": "on"},
            )
        self.assertEqual(response.status_code, 302)
        student.refresh_from_db()
        self.assertNotEqual(student.image.name, old_name)
        self.assertFalse(self.storage.exists(old_name))
        self.assertTrue(self.storage.exists(student.image.name))
        self.assertFalse(student.authorized)
        self.assertEqual(student.embeddings.count(), 1)

    def test_old_file_kept_if_transaction_rolls_back(self):
        student = make_student()
        old_name = student.image.name
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.client.post(
                reverse("student-photo-replace", args=[student.pk]),
                {"photo": upload(image_bytes(BLUE)), "consent": "on"},
            )
        # Callbacks were captured but not run (as on rollback): the old file still exists.
        self.assertTrue(callbacks)
        self.assertTrue(self.storage.exists(old_name))

    def test_remove_biometrics_deletes_photo_and_templates_but_keeps_attendance(self):
        student = make_student()
        student.attendance_set.create(date="2025-01-02")
        name = student.image.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("student-remove-biometrics", args=[student.pk]),
                             {"reason": "Student withdrew consent in writing"})
        student.refresh_from_db()
        self.assertFalse(student.image)
        self.assertFalse(student.authorized)
        self.assertEqual(student.consent_status, Student.Consent.WITHDRAWN)
        self.assertEqual(student.embeddings.count(), 0)
        self.assertFalse(self.storage.exists(name))
        self.assertEqual(student.attendance_set.count(), 1)
        event = AuditEvent.objects.get(action="biometrics_removed")
        self.assertEqual(event.reason, "Student withdrew consent in writing")

    def test_remove_biometrics_requires_reason(self):
        student = make_student()
        response = self.client.post(reverse("student-remove-biometrics", args=[student.pk]), {"reason": ""})
        self.assertEqual(response.status_code, 200)
        student.refresh_from_db()
        self.assertTrue(student.image)

    def test_delete_student_removes_file(self):
        student = make_student()
        name = student.image.name
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("student-delete", args=[student.pk]))
        self.assertRedirects(response, reverse("student-list"))
        self.assertFalse(Student.objects.filter(pk=student.pk).exists())
        self.assertFalse(self.storage.exists(name))
        self.assertEqual(FaceEmbedding.objects.count(), 0)
        event = AuditEvent.objects.get(action="student_deleted")
        self.assertEqual(event.details, {"student_id": student.pk})

    def test_delete_blocked_when_attendance_exists(self):
        student = make_student()
        student.attendance_set.create(date="2025-01-02")
        response = self.client.post(reverse("student-delete", args=[student.pk]), follow=True)
        self.assertContains(response, "attendance record")
        self.assertTrue(Student.objects.filter(pk=student.pk).exists())

    def test_edit_details_is_audited_without_values(self):
        student = make_student()
        self.client.post(reverse("student-edit", args=[student.pk]), {
            "name": "Changed Name", "email": student.email,
            "registration_number": student.registration_number, "branch": "IT", "year": "1st Year",
        })
        event = AuditEvent.objects.get(action="details_changed")
        self.assertEqual(event.details, {"fields": ["branch", "name"]})
