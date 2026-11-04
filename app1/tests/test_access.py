"""Who can reach what: anonymous, authenticated-without-role, Instructor and Admin."""
from django.test import override_settings
from django.urls import reverse

from .helpers import AppTestCase, make_student, make_user


class AccessMatrixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.student = make_student()
        attendance = self.student.attendance_set.create(date="2025-01-01")
        self.pages = {
            "home": reverse("home"),
            "kiosk": reverse("kiosk"),
            "students": reverse("student-list"),
            "detail": reverse("student-detail", args=[self.student.pk]),
            "photo": reverse("student-photo", args=[self.student.pk]),
            "enroll": reverse("student-enroll"),
            "edit": reverse("student-edit", args=[self.student.pk]),
            "replace": reverse("student-photo-replace", args=[self.student.pk]),
            "authorize": reverse("student-authorize", args=[self.student.pk]),
            "remove": reverse("student-remove-biometrics", args=[self.student.pk]),
            "delete": reverse("student-delete", args=[self.student.pk]),
            "attendance": reverse("student_attendance_list"),
            "export": reverse("attendance-export"),
            "manual": reverse("attendance-manual"),
            "correct": reverse("attendance-correct", args=[attendance.pk]),
            "audit": reverse("audit-log"),
        }

    def assert_statuses(self, expected):
        for name, status in expected.items():
            with self.subTest(page=name):
                self.assertEqual(self.client.get(self.pages[name]).status_code, status)

    def test_anonymous_is_redirected_to_login_everywhere(self):
        for name, url in self.pages.items():
            with self.subTest(page=name):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("login") + "?next="))

    def test_anonymous_json_endpoints_return_401(self):
        for url in (reverse("kiosk-recognize"), reverse("kiosk-confirm")):
            response = self.client.post(url)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["status"], "unauthenticated")

    def test_public_pages(self):
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
        self.assertEqual(self.client.get(reverse("privacy-notice")).status_code, 200)

    def test_authenticated_user_without_role_gets_403(self):
        self.login(make_user("nobody"))
        self.assert_statuses({name: 403 for name in self.pages})
        response = self.client.post(reverse("kiosk-recognize"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["status"], "forbidden")

    def test_instructor_permissions(self):
        self.login(self.instructor())
        self.assert_statuses({
            "home": 200, "kiosk": 200, "students": 200, "detail": 200, "photo": 200,
            "attendance": 200, "export": 200, "manual": 200, "correct": 200,
            "enroll": 403, "edit": 403, "replace": 403, "authorize": 403, "remove": 403,
            "delete": 403, "audit": 403,
        })

    def test_admin_group_permissions(self):
        self.login(self.admin_user())
        self.assert_statuses({name: 200 for name in self.pages})

    def test_superuser_has_everything(self):
        self.login(make_user("root", superuser=True))
        self.assert_statuses({name: 200 for name in self.pages})

    def test_photo_requires_specific_permission(self):
        self.login(make_user("viewer", perms=["app1.view_student", "app1.view_all_batches"]))
        self.assertEqual(self.client.get(self.pages["detail"]).status_code, 200)
        self.assertEqual(self.client.get(self.pages["photo"]).status_code, 403)


class PrivateMediaTests(AppTestCase):
    def test_photo_response_is_private_and_not_cached(self):
        student = make_student()
        self.login(self.instructor())
        response = self.client.get(reverse("student-photo", args=[student.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertTrue(b"".join(response.streaming_content).startswith(b"\xff\xd8"))

    def test_stored_file_name_is_random_and_not_derived_from_the_name(self):
        student = make_student(name="Distinctive Name")
        self.assertNotIn("Distinctive", student.image.name)
        self.assertRegex(student.image.name, r"^students/[0-9a-f]{32}\.jpg$")

    @override_settings(DEBUG=True)
    def test_no_public_media_route_even_in_debug(self):
        student = make_student()
        self.login(self.admin_user())
        for url in (f"/media/{student.image.name}", f"/{student.image.name}", "/private_media/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_missing_photo_file_is_404_not_500(self):
        student = make_student()
        from app1.storage import PrivateMediaStorage

        PrivateMediaStorage().delete(student.image.name)
        self.login(self.instructor())
        self.assertEqual(self.client.get(reverse("student-photo", args=[student.pk])).status_code, 404)

    def test_storage_has_no_public_url(self):
        student = make_student()
        with self.assertRaises(ValueError):
            student.image.url  # noqa: B018


class SecurityHeaderTests(AppTestCase):
    def test_headers_on_pages(self):
        response = self.client.get(reverse("login"))
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertIn("camera=(self)", response["Permissions-Policy"])
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_custom_error_pages_do_not_leak_details(self):
        self.login(make_user("nobody"))
        response = self.client.get(reverse("student-list"))
        self.assertContains(response, "Access denied", status_code=403)
        response = self.client.get("/does-not-exist/")
        self.assertContains(response, "Page not found", status_code=404)
        self.assertNotContains(response, "urlpatterns", status_code=404)
