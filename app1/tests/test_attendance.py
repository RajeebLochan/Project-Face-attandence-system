"""Attendance correctness: dates, idempotency, constraints, corrections, reports."""
import datetime as dt
from unittest import mock

from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app1.models import Attendance, AuditEvent
from app1.services.attendance import (
    CHECK_IN,
    CHECK_OUT,
    AttendanceError,
    correct_attendance,
    record_event,
)

from .helpers import AppTestCase, make_student

UTC = dt.UTC


def utc(*args):
    return dt.datetime(*args, tzinfo=UTC)


class LocalDateTests(AppTestCase):
    """TIME_ZONE is Asia/Kolkata (UTC+05:30). The old code filed 00:00–05:29 IST under yesterday."""

    def setUp(self):
        super().setUp()
        self.student = make_student()
        self.staff = self.instructor()

    def check_in_at(self, moment):
        with mock.patch("django.utils.timezone.now", return_value=moment):
            return record_event(self.student, CHECK_IN, actor=self.staff, source="kiosk")

    def test_boundaries(self):
        cases = [
            (utc(2025, 6, 1, 18, 29, 59), dt.date(2025, 6, 1)),  # 23:59:59 IST
            (utc(2025, 6, 1, 18, 30, 0), dt.date(2025, 6, 2)),  # 00:00:00 IST
            (utc(2025, 6, 1, 20, 30, 0), dt.date(2025, 6, 2)),  # 02:00 IST (old bug window)
            (utc(2025, 6, 1, 23, 59, 0), dt.date(2025, 6, 2)),  # 05:29 IST
            (utc(2025, 6, 2, 0, 1, 0), dt.date(2025, 6, 2)),  # 05:31 IST
        ]
        for moment, expected in cases:
            with self.subTest(moment=moment):
                Attendance.objects.all().delete()
                outcome = self.check_in_at(moment)
                self.assertEqual(outcome.attendance.date, expected)

    def test_repeated_frames_in_bug_window_create_one_row(self):
        moment = utc(2025, 6, 1, 20, 30)
        outcomes = [self.check_in_at(moment + dt.timedelta(seconds=i)) for i in range(5)]
        self.assertEqual([o.status for o in outcomes], ["recorded"] + ["already_recorded"] * 4)
        self.assertEqual(Attendance.objects.count(), 1)
        self.assertEqual(Attendance.objects.get().check_in_time, moment)
        self.assertEqual(AuditEvent.objects.filter(action="check_in").count(), 1)


class RecordEventTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.student = make_student()
        self.staff = self.instructor()

    def test_check_out_requires_check_in(self):
        with self.assertRaises(AttendanceError) as ctx:
            record_event(self.student, CHECK_OUT, actor=self.staff, source="kiosk")
        self.assertEqual(ctx.exception.code, "not_checked_in")
        self.assertEqual(Attendance.objects.count(), 0)

    def test_explicit_modes_no_automatic_checkout(self):
        record_event(self.student, CHECK_IN, actor=self.staff, source="kiosk")
        # Seeing the student again in check-in mode, any time later, never checks them out.
        later = timezone.now() + dt.timedelta(hours=2)
        with mock.patch("django.utils.timezone.now", return_value=later):
            outcome = record_event(self.student, CHECK_IN, actor=self.staff, source="kiosk", at=later)
        self.assertEqual(outcome.status, "already_recorded")
        self.assertIsNone(Attendance.objects.get().check_out_time)
        record_event(self.student, CHECK_OUT, actor=self.staff, source="kiosk")
        again = record_event(self.student, CHECK_OUT, actor=self.staff, source="kiosk")
        self.assertEqual(again.status, "already_recorded")
        self.assertEqual(AuditEvent.objects.filter(action="check_out").count(), 1)

    def test_rejects_future_and_naive_times(self):
        future = timezone.now() + dt.timedelta(hours=1)
        with self.assertRaises(AttendanceError):
            record_event(self.student, CHECK_IN, actor=self.staff, source="manual", at=future)
        with self.assertRaises(AttendanceError):
            record_event(self.student, CHECK_IN, actor=self.staff, source="manual", at=dt.datetime(2025, 1, 1, 9))

    def test_failure_rolls_back_attendance_and_audit(self):
        with mock.patch("app1.services.attendance.AuditEvent.objects.create", side_effect=RuntimeError("db")):
            with self.assertRaises(RuntimeError):
                record_event(self.student, CHECK_IN, actor=self.staff, source="kiosk")
        self.assertEqual(Attendance.objects.count(), 0)


class ConstraintTests(AppTestCase):
    def test_unique_student_date(self):
        student = make_student()
        Attendance.objects.create(student=student, date=dt.date(2025, 1, 1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attendance.objects.create(student=student, date=dt.date(2025, 1, 1))

    def test_checkout_requires_checkin_at_database_level(self):
        student = make_student()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attendance.objects.create(student=student, date=dt.date(2025, 1, 1), check_out_time=timezone.now())

    def test_unique_registration_number(self):
        make_student(registration_number="DUP-1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_student(registration_number="DUP-1")

    def test_attendance_history_protects_student_row(self):
        from django.db.models import ProtectedError

        student = make_student()
        Attendance.objects.create(student=student, date=dt.date(2025, 1, 1))
        with self.assertRaises(ProtectedError):
            student.delete()


class ManualAndCorrectionTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.student = make_student()
        self.staff = self.login(self.instructor())

    def test_manual_entry_requires_reason_and_is_audited(self):
        today = timezone.localdate()
        data = {"student": self.student.pk, "mode": "check_in", "date": today.isoformat(), "time": "00:01"}
        response = self.client.post(reverse("attendance-manual-past"), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Attendance.objects.count(), 0)
        data["reason"] = "Camera not working"
        response = self.client.post(reverse("attendance-manual-past"), data)
        self.assertRedirects(response, reverse("student_attendance_list"))
        record = Attendance.objects.get()
        self.assertEqual(record.date, today)
        event = AuditEvent.objects.get(action="check_in")
        self.assertEqual((event.source, event.actor, event.reason), ("manual", self.staff, "Camera not working"))

    def test_manual_entry_rejects_future(self):
        tomorrow = timezone.localdate() + dt.timedelta(days=1)
        response = self.client.post(reverse("attendance-manual-past"), {
            "student": self.student.pk, "mode": "check_in", "date": tomorrow.isoformat(),
            "time": "09:00", "reason": "future test",
        })
        self.assertContains(response, "cannot be recorded in the future")
        self.assertEqual(Attendance.objects.count(), 0)

    def test_correction_records_before_and_after(self):
        day = timezone.localdate() - dt.timedelta(days=1)
        moment = timezone.make_aware(dt.datetime.combine(day, dt.time(9, 0)))
        record = Attendance.objects.create(student=self.student, date=day, check_in_time=moment)
        response = self.client.post(reverse("attendance-correct", args=[record.pk]), {
            "check_in": "08:45", "check_out": "15:30", "reason": "Signed register shows 08:45",
        })
        self.assertRedirects(response, reverse("student_attendance_list"))
        record.refresh_from_db()
        self.assertEqual(timezone.localtime(record.check_in_time).time(), dt.time(8, 45))
        self.assertEqual(timezone.localtime(record.check_out_time).time(), dt.time(15, 30))
        event = AuditEvent.objects.get(action="attendance_corrected")
        self.assertEqual(event.reason, "Signed register shows 08:45")
        self.assertEqual(event.details["before"]["check_in_time"], moment.isoformat())
        self.assertIsNone(event.details["before"]["check_out_time"])
        self.assertIsNotNone(event.details["after"]["check_out_time"])

    def test_correction_validation(self):
        day = timezone.localdate() - dt.timedelta(days=1)
        record = Attendance.objects.create(student=self.student, date=day,
                                           check_in_time=timezone.make_aware(dt.datetime.combine(day, dt.time(9))))
        url = reverse("attendance-correct", args=[record.pk])
        bad = [
            {"check_in": "09:00", "check_out": "10:00", "reason": ""},
            {"check_in": "", "check_out": "10:00", "reason": "checkout only"},
            {"check_in": "11:00", "check_out": "10:00", "reason": "reversed order"},
            {"check_in": "nonsense", "reason": "bad value here"},
        ]
        for data in bad:
            with self.subTest(data=data):
                self.assertEqual(self.client.post(url, data).status_code, 200)
        self.assertFalse(AuditEvent.objects.filter(action="attendance_corrected").exists())

    def test_service_rejects_times_on_other_dates(self):
        day = timezone.localdate() - dt.timedelta(days=1)
        record = Attendance.objects.create(student=self.student, date=day)
        other = timezone.make_aware(dt.datetime.combine(day - dt.timedelta(days=1), dt.time(9)))
        with self.assertRaises(AttendanceError):
            correct_attendance(record, check_in=other, check_out=None, actor=self.staff, reason="wrong day")


class ReportTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.instructor())

    def test_invalid_filters_are_errors_not_500(self):
        url = reverse("student_attendance_list")
        for query in ("date_from=foo", "date_to=2025-13-45", "date_from=2025-02-01&date_to=2025-01-01", "page=abc"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(f"{url}?{query}").status_code, 200)
        response = self.client.get(f"{url}?date_from=2025-02-01&date_to=2025-01-01")
        self.assertContains(response, "must be on or before")

    def test_filters_and_query_count_do_not_grow_with_rows(self):
        url = reverse("student_attendance_list")

        def add(count, offset):
            for i in range(count):
                student = make_student(name=f"Student {offset + i}", embedding=False)
                Attendance.objects.create(student=student, date=dt.date(2025, 1, 1 + (offset + i) % 3))

        add(5, 0)
        self.client.get(url)  # warm-up: creates the settings row once
        with CaptureQueriesContext(connection) as small:
            self.client.get(url)
        add(30, 5)
        with CaptureQueriesContext(connection) as large:
            self.client.get(url)
        self.assertEqual(len(small), len(large))
        response = self.client.get(url, {"date_from": "2025-01-02", "date_to": "2025-01-02"})
        self.assertEqual(response.context["page"].paginator.count, 12)

    def test_student_list_query_count_is_constant(self):
        url = reverse("student-list")
        for i in range(3):
            make_student(name=f"Student {i}", embedding=False)
        self.client.get(url)  # warm-up: creates the settings row once
        with CaptureQueriesContext(connection) as small:
            self.client.get(url)
        for i in range(30):
            make_student(name=f"More {i}", embedding=False)
        with CaptureQueriesContext(connection) as large:
            response = self.client.get(url)
        self.assertEqual(len(small), len(large))
        self.assertEqual(len(response.context["page"].object_list), 25)

    def test_csv_export_escapes_formulas(self):
        student = make_student(name="=HYPERLINK(\"http://x\")")
        Attendance.objects.create(student=student, date=dt.date(2025, 1, 1), check_in_time=utc(2025, 1, 1, 3, 30))
        response = self.client.get(reverse("attendance-export"))
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("no-store", response["Cache-Control"])
        body = response.content.decode()
        self.assertIn("'=HYPERLINK", body)
        self.assertIn("2025-01-01 09:00:00", body)  # 03:30 UTC shown as local IST time
