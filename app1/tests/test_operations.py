"""Schedules and statuses, roster scoping, manual check-in/out, import, reports, staff and settings."""

import datetime as dt
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app1.models import (
    Attendance,
    AuditEvent,
    Batch,
    BatchMembership,
    BatchSchedule,
    Holiday,
    InstitutionSettings,
    Leave,
    Student,
)
from app1.services.attendance import CHECK_IN, AttendanceError, record_event, review_missing_checkout
from app1.services.register import RegisterContext, statuses_for_day

from .helpers import AppTestCase, default_batch, image_bytes, make_student, make_user, upload

MONDAY = dt.date(2025, 6, 2)  # a Monday


def aware(day, hour, minute):
    return timezone.make_aware(dt.datetime.combine(day, dt.time(hour, minute)))


class StatusRuleTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.student = make_student()
        self.batch = default_batch()

    def status(self, day, now_day=None):
        with mock.patch("django.utils.timezone.localdate", return_value=now_day or dt.date(2025, 7, 1)):
            return statuses_for_day([self.student], day)[0].status

    def schedule(self, **kwargs):
        values = {"effective_from": dt.date(2025, 1, 1), "start_time": dt.time(9, 0), "grace_minutes": 10}
        values.update(kwargs)
        return BatchSchedule.objects.create(batch=self.batch, **values)

    def test_without_schedule_nothing_is_inferred(self):
        self.assertEqual(self.status(MONDAY), "not_recorded")
        Attendance.objects.create(student=self.student, date=MONDAY, check_in_time=aware(MONDAY, 11, 0))
        self.assertEqual(self.status(MONDAY), "checked_in")

    def test_present_late_and_grace_boundary(self):
        self.schedule()
        record = Attendance.objects.create(student=self.student, date=MONDAY, check_in_time=aware(MONDAY, 9, 10))
        self.assertEqual(self.status(MONDAY), "present")  # exactly start + grace is on time
        record.check_in_time = aware(MONDAY, 9, 11)
        record.save()
        self.assertEqual(self.status(MONDAY), "late")

    def test_absent_only_for_past_working_days(self):
        self.schedule()
        self.assertEqual(self.status(MONDAY), "absent")
        self.assertEqual(self.status(MONDAY, now_day=MONDAY), "not_recorded")
        self.assertEqual(self.status(MONDAY + dt.timedelta(days=5)), "no_class")  # Saturday

    def test_holidays_leave_and_enrolment(self):
        self.schedule()
        Holiday.objects.create(date=MONDAY, name="Synthetic holiday")
        self.assertEqual(self.status(MONDAY), "holiday")
        tuesday = MONDAY + dt.timedelta(days=1)
        Leave.objects.create(student=self.student, start_date=tuesday, end_date=tuesday, reason="Synthetic")
        self.assertEqual(self.status(tuesday), "on_leave")
        Student.objects.filter(pk=self.student.pk).update(left_on=tuesday + dt.timedelta(days=1))
        self.student.refresh_from_db()
        self.assertEqual(self.status(tuesday + dt.timedelta(days=1)), "not_enrolled")
        self.assertEqual(self.status(dt.date(2019, 6, 3)), "not_enrolled")

    def test_effective_dated_schedule_does_not_rewrite_the_past(self):
        self.schedule()
        Attendance.objects.create(student=self.student, date=MONDAY, check_in_time=aware(MONDAY, 9, 20))
        self.assertEqual(self.status(MONDAY), "late")
        self.schedule(effective_from=MONDAY + dt.timedelta(days=7), start_time=dt.time(9, 30))
        self.assertEqual(self.status(MONDAY), "late")
        next_monday = MONDAY + dt.timedelta(days=7)
        Attendance.objects.create(student=self.student, date=next_monday, check_in_time=aware(next_monday, 9, 20))
        self.assertEqual(self.status(next_monday), "present")

    def test_roster_moves_keep_history(self):
        self.schedule()
        other = Batch.objects.create(name="Other", code="OTHER-1")
        BatchSchedule.objects.create(
            batch=other, effective_from=dt.date(2025, 1, 1), weekdays="5", start_time=dt.time(8, 0), grace_minutes=0
        )
        move = MONDAY + dt.timedelta(days=3)
        BatchMembership.objects.filter(student=self.student).update(end_date=move)
        BatchMembership.objects.create(student=self.student, batch=other, start_date=move)
        self.assertEqual(self.status(MONDAY), "absent")  # old batch: Monday is a working day
        self.assertEqual(self.status(move + dt.timedelta(days=3)), "no_class")  # new batch: Saturdays only

    def test_checkout_missing_flag_and_review(self):
        day = timezone.localdate() - dt.timedelta(days=2)
        record = Attendance.objects.create(student=self.student, date=day, check_in_time=aware(day, 9, 0))
        context = RegisterContext([self.student], day, day)
        self.assertTrue(context.status(self.student, day).checkout_missing)
        admin = self.admin_user()
        self.assertTrue(review_missing_checkout(record, actor=admin, reason="Left early, no record"))
        record.refresh_from_db()
        self.assertIsNone(record.check_out_time)
        self.assertIsNotNone(record.checkout_review_at)
        self.assertTrue(AuditEvent.objects.filter(action="checkout_reviewed", reason="Left early, no record").exists())
        with self.assertRaises(AttendanceError):
            review_missing_checkout(record, actor=admin, reason="")


class ManualTransitionTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.student = make_student(authorized=False, consent=False, photo=False, embedding=False)
        self.staff = self.login(self.instructor())

    def post(self, mode, reason="Camera unavailable"):
        return self.client.post(
            reverse("attendance-manual-record"),
            {"student": self.student.pk, "mode": mode, "reason": reason},
            follow=True,
        )

    def test_students_without_consent_are_supported(self):
        response = self.post("check_in")
        self.assertContains(response, "Check-in recorded")
        event = AuditEvent.objects.get(action="check_in")
        self.assertEqual((event.source, event.actor, event.reason), ("manual", self.staff, "Camera unavailable"))

    def test_repeated_clicks_are_idempotent(self):
        self.post("check_in")
        response = self.post("check_in")
        self.assertContains(response, "already recorded")
        self.assertEqual(AuditEvent.objects.filter(action="check_in").count(), 1)

    def test_check_out_before_check_in_and_reason_required(self):
        self.assertContains(self.post("check_out"), "has not checked in today")
        self.assertContains(self.post("check_in", reason=""), "Give a reason")
        self.assertEqual(Attendance.objects.count(), 0)

    def test_kiosk_and_manual_share_transition_rules(self):
        record_event(self.student, CHECK_IN, actor=self.staff, source="kiosk")
        self.assertContains(self.post("check_in"), "already recorded")
        self.assertContains(self.post("check_out"), "Check-out recorded")

    def test_inactive_students_are_refused(self):
        Student.objects.filter(pk=self.student.pk).update(left_on=timezone.localdate())
        self.assertContains(self.post("check_in"), "not on the register")

    def test_instructor_cannot_reach_other_batches(self):
        other_batch = Batch.objects.create(name="Elsewhere", code="ELSE-1")
        outsider = make_student(batch=other_batch)
        response = self.client.post(
            reverse("attendance-manual-record"), {"student": outsider.pk, "mode": "check_in", "reason": "trying"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(reverse("student-detail", args=[outsider.pk])).status_code, 404)
        record = Attendance.objects.create(student=outsider, date=timezone.localdate())
        self.assertEqual(self.client.get(reverse("attendance-correct", args=[record.pk])).status_code, 404)
        search = self.client.get(reverse("attendance-manual"), {"q": outsider.name})
        self.assertNotContains(search, outsider.registration_number)
        export = self.client.get(reverse("attendance-export")).content.decode()
        self.assertNotIn(outsider.registration_number, export)

    def test_kiosk_candidate_outside_instructor_batches_is_unknown(self):
        other_batch = Batch.objects.create(name="Elsewhere", code="ELSE-2")
        outsider = make_student(batch=other_batch)
        response = self.client.post(reverse("kiosk-recognize"), {"mode": "check_in", "frame": upload(image_bytes())})
        self.assertEqual(response.json()["status"], "unknown")
        self.assertNotContains(response, outsider.name)


class AdminScreensTests(AppTestCase):
    def test_new_screens_are_permission_checked(self):
        urls = [
            reverse(name)
            for name in ("settings", "staff-list", "staff-new", "notifications", "student-import", "batch-new")
        ]
        self.login(self.instructor())
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        for url in (reverse("reports"), reverse("register-day"), reverse("batch-list"), reverse("leave-list")):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.login(self.admin_user())
        for url in urls:
            with self.subTest(url=url, role="admin"):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_health_endpoints(self):
        self.assertEqual(self.client.get("/healthz").content, b"ok")
        response = self.client.get("/readyz")
        self.assertEqual(response.json(), {"status": "ready"})

    def test_enabling_notifications_requires_configuration(self):
        self.login(self.admin_user())
        response = self.client.post(reverse("settings"), {"notifications_enabled": "on", "digest_time": "18:00"})
        self.assertContains(response, "cannot be switched on yet")
        self.assertFalse(InstitutionSettings.load().notifications_enabled)
        self.client.post(
            reverse("settings"),
            {
                "name": "Synthetic Institute",
                "contact_email": "office@institute.example",
                "notifications_enabled": "on",
                "notify_check_in": "on",
                "digest_time": "18:00",
            },
        )
        self.assertTrue(InstitutionSettings.load().notifications_enabled)

    def test_staff_management(self):
        admin = self.login(self.admin_user())
        batch = default_batch()
        response = self.client.post(
            reverse("staff-new"),
            {
                "username": "new.teacher",
                "email": "new.teacher@institute.example",
                "role": "instructor",
                "batches": [batch.pk],
                "is_active": "on",
                "password": "a-long-synthetic-pass-42",
            },
        )
        user = type(admin).objects.get(username="new.teacher")
        self.assertRedirects(response, reverse("staff-edit", args=[user.pk]))
        self.assertEqual(list(user.assigned_batches.all()), [batch])
        self.assertTrue(user.check_password("a-long-synthetic-pass-42"))
        response = self.client.post(
            reverse("staff-edit", args=[admin.pk]),
            {
                "username": admin.username,
                "role": "instructor",
                "is_active": "on",
            },
        )
        self.assertContains(response, "cannot change your own role")
        root = make_user("root", superuser=True)
        self.assertEqual(self.client.get(reverse("staff-edit", args=[root.pk])).status_code, 404)

    def test_schedule_cannot_start_in_the_past(self):
        self.login(self.admin_user())
        batch = default_batch()
        yesterday = timezone.localdate() - dt.timedelta(days=1)
        self.client.post(
            reverse("schedule-add", args=[batch.pk]),
            {"effective_from": yesterday, "weekdays": ["0"], "start_time": "09:00", "grace_minutes": 5},
        )
        self.assertFalse(BatchSchedule.objects.exists())
        self.client.post(
            reverse("schedule-add", args=[batch.pk]),
            {"effective_from": timezone.localdate(), "weekdays": ["0", "1"], "start_time": "09:00", "grace_minutes": 5},
        )
        self.assertEqual(BatchSchedule.objects.get().weekdays, "0,1")

    def test_leave_and_holiday_are_audited(self):
        self.login(self.admin_user())
        student = make_student()
        today = timezone.localdate()
        self.client.post(
            reverse("leave-add"),
            {"student": student.pk, "start_date": today, "end_date": today, "reason": "Synthetic leave"},
        )
        leave = Leave.objects.get()
        self.client.post(reverse("leave-cancel", args=[leave.pk]))
        self.client.post(reverse("holiday-add"), {"date": today, "name": "Synthetic day"})
        actions = set(AuditEvent.objects.values_list("action", flat=True))
        self.assertTrue({"leave_recorded", "leave_cancelled", "holiday_changed"} <= actions)

    def test_report_range_and_export(self):
        self.login(self.instructor())
        student = make_student()
        BatchSchedule.objects.create(
            batch=default_batch(), effective_from=dt.date(2025, 1, 1), start_time=dt.time(9, 0)
        )
        Attendance.objects.create(student=student, date=MONDAY, check_in_time=aware(MONDAY, 9, 30))
        too_long = self.client.get(reverse("reports"), {"date_from": "2025-01-01", "date_to": "2025-06-30"})
        self.assertContains(too_long, "at most 62 days")
        export = self.client.get(reverse("report-export"), {"date_from": MONDAY, "date_to": MONDAY})
        body = export.content.decode()
        self.assertIn(f"{MONDAY.isoformat()},{student.registration_number}", body)
        self.assertIn("Late", body)


class ImportTests(AppTestCase):
    HEADER = "name,email,registration_number,branch,year,batch_code,enrolled_on,guardian_name,guardian_email\n"

    def setUp(self):
        super().setUp()
        self.admin = self.login(self.admin_user())
        make_student(registration_number="EXIST-1")
        default_batch()

    def upload_csv(self, body):
        file = SimpleUploadedFile("students.csv", (self.HEADER + body).encode(), content_type="text/csv")
        return self.client.post(reverse("student-import"), {"file": file})

    def test_preview_classifies_rows_then_confirm_creates(self):
        response = self.upload_csv(
            "Asha Verma,asha@students.example,NEW-001,CSE,1st Year,TEST-A,2025-07-01,Ravi Verma,ravi@family.example\n"
            "Same Again,same@students.example,new-001,CSE,1st Year,,,,\n"
            "Old One,old@students.example,EXIST-1,CSE,1st Year,,,,\n"
            "Bad Row,not-an-email,NEW-002,XYZ,9th Year,NOPE,07/01/2025,,bad\n"
        )
        counts = response.context["counts"]
        self.assertEqual(counts, {"new": 1, "exists": 1, "duplicate_in_file": 1, "invalid": 1})
        self.assertEqual(Student.objects.count(), 1)  # nothing created by the preview
        self.client.post(reverse("student-import-confirm"), {"token": response.context["token"]})
        created = Student.objects.get(registration_number="NEW-001")
        self.assertEqual(
            (created.enrolled_on, created.consent_status, bool(created.image)),
            (dt.date(2025, 7, 1), "not_recorded", False),
        )
        self.assertEqual(created.memberships.get().batch.code, "TEST-A")
        self.assertTrue(AuditEvent.objects.filter(action="students_imported").exists())

    def test_bad_files(self):
        self.assertContains(self.upload_csv(""), "no student rows")
        missing = SimpleUploadedFile("x.csv", b"name,email\nA,b@c.d\n", content_type="text/csv")
        self.assertContains(self.client.post(reverse("student-import"), {"file": missing}), "Missing columns")
        latin = SimpleUploadedFile("x.csv", (self.HEADER + "Jos\xe9,a@b.c,X-1,CSE,1st Year,,,,\n").encode("latin-1"))
        self.assertContains(self.client.post(reverse("student-import"), {"file": latin}), "UTF-8")

    def test_confirm_requires_matching_preview(self):
        response = self.client.post(reverse("student-import-confirm"), {"token": "forged"}, follow=True)
        self.assertContains(response, "preview expired")
