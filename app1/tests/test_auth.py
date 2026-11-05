"""Login, logout, redirects, throttling, CSRF and POST-only mutations."""
from django.test import Client, override_settings
from django.urls import reverse

from app1.models import Student

from .helpers import PASSWORD, AppTestCase, image_bytes, make_student, make_user, upload


class LoginLogoutTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.instructor()

    def test_login_redirects_to_safe_next(self):
        response = self.client.post(
            reverse("login"), {"username": self.user.username, "password": PASSWORD, "next": "/students/"}
        )
        self.assertRedirects(response, "/students/", fetch_redirect_response=False)

    def test_login_ignores_external_next(self):
        for target in ("https://evil.example/", "//evil.example/", "javascript:alert(1)"):
            with self.subTest(target=target):
                self.client.logout()
                response = self.client.post(
                    reverse("login"), {"username": self.user.username, "password": PASSWORD, "next": target}
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], reverse("home"))

    def test_logout_requires_post(self):
        self.login(self.user)
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)
        response = self.client.post(reverse("logout"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_failed_login_shows_generic_error(self):
        response = self.client.post(reverse("login"), {"username": self.user.username, "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please enter a correct username and password")


@override_settings(LOGIN_THROTTLE_MAX_FAILURES=3, LOGIN_THROTTLE_MAX_FAILURES_PER_IP=6)
class LoginThrottleTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.instructor()

    def attempt(self, username, password, ip="10.0.0.1"):
        return self.client.post(reverse("login"), {"username": username, "password": password}, REMOTE_ADDR=ip)

    def test_username_locked_after_repeated_failures_even_with_right_password(self):
        for _ in range(3):
            self.assertEqual(self.attempt(self.user.username, "wrong").status_code, 200)
        response = self.attempt(self.user.username, PASSWORD)
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many failed attempts", status_code=429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_lock_is_case_insensitive_and_per_username(self):
        for _ in range(3):
            self.attempt(self.user.username.upper(), "wrong")
        self.assertEqual(self.attempt(self.user.username, PASSWORD).status_code, 429)
        other = make_user("other")
        self.assertEqual(self.attempt(other.username, PASSWORD).status_code, 302)

    def test_address_limit(self):
        for index in range(6):
            self.attempt(f"user{index}", "wrong", ip="10.0.0.9")
        self.assertEqual(self.attempt(self.user.username, PASSWORD, ip="10.0.0.9").status_code, 429)
        self.assertEqual(self.attempt(self.user.username, PASSWORD, ip="10.0.0.10").status_code, 302)

    def test_success_resets_username_counter(self):
        for _ in range(2):
            self.attempt(self.user.username, "wrong")
        self.assertEqual(self.attempt(self.user.username, PASSWORD).status_code, 302)
        self.client.logout()
        for _ in range(2):
            self.attempt(self.user.username, "wrong")
        self.assertEqual(self.attempt(self.user.username, PASSWORD).status_code, 302)


class CsrfTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.csrf_client = Client(enforce_csrf_checks=True)
        self.admin = self.admin_user()
        self.csrf_client.force_login(self.admin)
        self.student = make_student()

    def test_mutations_without_token_are_rejected(self):
        posts = {
            reverse("logout"): {},
            reverse("student-enroll"): {},
            reverse("student-authorize", args=[self.student.pk]): {"decision": "revoke"},
            reverse("student-delete", args=[self.student.pk]): {},
            reverse("student-remove-biometrics", args=[self.student.pk]): {"reason": "testing csrf"},
            reverse("attendance-manual"): {},
            reverse("kiosk-recognize"): {"mode": "check_in", "frame": upload(image_bytes())},
            reverse("kiosk-confirm"): {"token": "x"},
        }
        for url, data in posts.items():
            with self.subTest(url=url):
                self.assertEqual(self.csrf_client.post(url, data).status_code, 403)
        self.student.refresh_from_db()
        self.assertTrue(self.student.authorized)
        self.assertTrue(Student.objects.filter(pk=self.student.pk).exists())

    def test_login_requires_token(self):
        anonymous = Client(enforce_csrf_checks=True)
        response = anonymous.post(reverse("login"), {"username": "x", "password": "y"})
        self.assertEqual(response.status_code, 403)


class PostOnlyMutationTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.admin_user())
        self.student = make_student()

    def test_get_never_mutates(self):
        self.client.get(reverse("student-authorize", args=[self.student.pk]) + "?decision=revoke")
        self.client.get(reverse("student-delete", args=[self.student.pk]))
        self.client.get(reverse("student-remove-biometrics", args=[self.student.pk]) + "?reason=xxxxxx")
        self.student.refresh_from_db()
        self.assertTrue(self.student.authorized)
        self.assertTrue(self.student.image)
        self.assertEqual(self.student.consent_status, Student.Consent.GIVEN)

    def test_json_endpoints_reject_get(self):
        self.assertEqual(self.client.get(reverse("kiosk-recognize")).status_code, 405)
        self.assertEqual(self.client.get(reverse("kiosk-confirm")).status_code, 405)

    def test_list_views_reject_post(self):
        self.assertEqual(self.client.post(reverse("student-list")).status_code, 405)
        self.assertEqual(self.client.post(reverse("student_attendance_list")).status_code, 405)
