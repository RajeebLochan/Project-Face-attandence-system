"""E-mail notifications: outbox creation, recipients, worker delivery, retries, digest, verification.

All mail goes to Django's in-memory backend; nothing is sent to a real server."""

import datetime as dt
import smtplib
import threading
from unittest import mock

from django.core import mail
from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from app1.models import Attendance, AuditEvent, InstitutionSettings, OutboundEmail, StaffNotificationPreference
from app1.notifications import digest, verification, worker
from app1.services.attendance import CHECK_IN, CHECK_OUT, correct_attendance, record_event

from .helpers import AppTestCase, image_bytes, make_student, make_user, upload

Status = OutboundEmail.Status


def enable(**flags):
    config = InstitutionSettings.load()
    config.name, config.contact_email = "Synthetic Institute", "office@institute.example"
    config.notifications_enabled = True
    for key, value in flags.items():
        setattr(config, key, value)
    config.save()
    return config


def verified_staff(name, **prefs):
    user = make_user(name, group="Attendance admin")
    user.email = f"{name}@institute.example"
    user.save()
    defaults = {"receive_check_in": True, "receive_check_out": True}
    defaults.update(prefs)
    StaffNotificationPreference.objects.create(
        user=user, verified_email=user.email, verified_at=timezone.now(), **defaults
    )
    return user


def guardian_student(**extra):
    student = make_student(name="Synthetic Student")
    student.guardian_email = "guardian@family.example"
    student.guardian_email_verified = "guardian@family.example"
    student.notify_guardian = True
    for key, value in extra.items():
        setattr(student, key, value)
    student.save()
    return student


class EnqueueTests(AppTestCase):
    def setUp(self):
        super().setUp()
        enable()
        self.staff = verified_staff("office")
        self.student = guardian_student()

    def check_in(self, **kwargs):
        return record_event(self.student, CHECK_IN, actor=self.staff, source="kiosk", **kwargs)

    def test_one_job_per_verified_recipient(self):
        self.check_in()
        jobs = OutboundEmail.objects.order_by("recipient_role")
        self.assertEqual(
            [(j.recipient_role, j.kind, j.status) for j in jobs],
            [("guardian", "check_in", "queued"), ("staff", "check_in", "queued")],
        )
        self.assertEqual(len(mail.outbox), 0)  # nothing is sent inside the request

    def test_unverified_or_opted_out_recipients_get_nothing(self):
        self.student.guardian_email_verified = ""
        self.student.notify_student = True  # student address never verified
        self.student.save()
        StaffNotificationPreference.objects.filter(user=self.staff).update(verified_email="old@institute.example")
        self.check_in()
        self.assertEqual(OutboundEmail.objects.count(), 0)

    def test_overlapping_addresses_are_deduplicated(self):
        self.student.guardian_email = self.student.guardian_email_verified = self.staff.email
        self.student.save()
        self.check_in()
        self.assertEqual(OutboundEmail.objects.count(), 1)
        self.assertEqual(OutboundEmail.objects.get().recipient_role, "staff")

    def test_disabled_settings_and_kinds(self):
        enable(notifications_enabled=False)
        self.check_in()
        self.assertEqual(OutboundEmail.objects.count(), 0)
        enable(notify_check_in=False)
        record_event(self.student, CHECK_OUT, actor=self.staff, source="kiosk")
        self.assertEqual(set(OutboundEmail.objects.values_list("kind", flat=True)), {"check_out"})

    def test_duplicate_no_op_submission_sends_nothing_more(self):
        self.check_in()
        self.check_in()
        self.check_in()
        self.assertEqual(OutboundEmail.objects.count(), 2)

    def test_rollback_leaves_no_email(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.check_in()
            raise RuntimeError("later failure in the same transaction")
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(OutboundEmail.objects.count(), 0)

    def test_notification_errors_never_block_attendance(self):
        with mock.patch("app1.notifications.core.render_event_email", side_effect=ValueError("template broke")):
            outcome = self.check_in()
        self.assertEqual(outcome.status, "recorded")
        self.assertEqual(OutboundEmail.objects.count(), 0)

    def test_one_job_per_event_recipient_and_kind_in_database(self):
        self.check_in()
        job = OutboundEmail.objects.first()
        with self.assertRaises(IntegrityError), transaction.atomic():
            OutboundEmail.objects.create(
                kind=job.kind,
                recipient_role=job.recipient_role,
                recipient_email=job.recipient_email,
                audit_event=job.audit_event,
                dedupe_key="different",
                subject="x",
                body_text="x",
            )

    def test_instructor_only_notified_about_own_batches(self):
        teacher = make_user("teacher-n", group="Instructor")
        teacher.email = "teacher@institute.example"
        teacher.save()
        StaffNotificationPreference.objects.create(
            user=teacher, receive_check_in=True, verified_email=teacher.email, verified_at=timezone.now()
        )
        self.check_in()
        self.assertFalse(OutboundEmail.objects.filter(recipient_email=teacher.email).exists())

    def test_past_day_entries_are_corrections_not_live_check_ins(self):
        yesterday = timezone.now() - dt.timedelta(days=1)
        self.check_in(at=yesterday)
        self.assertEqual(OutboundEmail.objects.count(), 0)  # corrections are off by default
        enable(notify_corrections=True)
        StaffNotificationPreference.objects.filter(user=self.staff).update(receive_corrections=True)
        record_event(self.student, CHECK_OUT, actor=self.staff, source="manual", at=yesterday + dt.timedelta(hours=1))
        self.assertEqual(set(OutboundEmail.objects.values_list("kind", flat=True)), {"correction"})

    def test_same_day_backdated_entry_says_it_was_entered_later(self):
        earlier = timezone.now() - dt.timedelta(minutes=45)
        if timezone.localdate(earlier) != timezone.localdate():
            self.skipTest("too close to midnight for a same-day backdated entry")
        self.check_in(at=earlier)
        job = OutboundEmail.objects.get(recipient_role="guardian")
        self.assertEqual(job.kind, "check_in")
        self.assertIn("Entered later by staff", job.body_text)

    def test_corrections_are_separate_events(self):
        enable(notify_corrections=True)
        StaffNotificationPreference.objects.filter(user=self.staff).update(receive_corrections=True)
        self.check_in()
        record = Attendance.objects.get()
        correct_attendance(
            record,
            check_in=record.check_in_time - dt.timedelta(minutes=5),
            check_out=None,
            actor=self.staff,
            reason="Register shows earlier time",
        )
        kinds = sorted(OutboundEmail.objects.filter(recipient_role="staff").values_list("kind", flat=True))
        self.assertEqual(kinds, ["check_in", "correction"])

    def test_kiosk_candidates_do_not_notify(self):
        self.login(self.instructor())
        self.client.post(reverse("kiosk-recognize"), {"mode": "check_in", "frame": upload(image_bytes())})
        self.assertEqual(OutboundEmail.objects.count(), 0)


class TemplateTests(AppTestCase):
    def setUp(self):
        super().setUp()
        enable(contact_phone="+91 80 0000 0000")
        self.staff = verified_staff("office")
        self.student = guardian_student()
        moment = timezone.make_aware(dt.datetime.combine(timezone.localdate(), dt.time(0, 5)))
        with mock.patch("django.utils.timezone.now", return_value=moment):
            record_event(self.student, CHECK_IN, actor=self.staff, source="manual", reason="camera down")

    def test_content_is_minimal_labelled_and_local(self):
        guardian = OutboundEmail.objects.get(recipient_role="guardian")
        for text in (guardian.body_text, guardian.body_html):
            self.assertIn("Synthetic Institute", text)
            self.assertIn(self.student.name, text)
            self.assertIn(self.student.registration_number, text)
            self.assertIn("00:05", text)
            self.assertIn("IST, Asia/Kolkata", text)
            self.assertIn("office@institute.example", text)
            self.assertIn("manually by staff", text)
            self.assertNotIn("/students/", text)  # no record links for students or guardians
            self.assertNotIn(self.student.email, text)
        staff = OutboundEmail.objects.get(recipient_role="staff")
        self.assertIn(f"https://attendance.example.test/students/{self.student.pk}/", staff.body_text)
        self.assertIn("staff login required", staff.body_text)

    def test_worker_sends_separate_html_and_text_messages_without_attachments(self):
        counts = worker.process_batch("test-worker")
        self.assertEqual(counts["sent"], 2)
        self.assertEqual(len(mail.outbox), 2)
        for message in mail.outbox:
            self.assertEqual(len(message.to), 1)
            self.assertEqual(message.attachments, [])
            self.assertEqual(message.alternatives[0][1], "text/html")
            self.assertTrue(message.subject.startswith("[Attendance] Check-in recorded"))
        self.assertEqual(set(OutboundEmail.objects.values_list("status", flat=True)), {Status.SENT})


class WorkerTests(AppTestCase):
    def setUp(self):
        super().setUp()
        enable()
        self.staff = verified_staff("office")
        self.student = guardian_student()
        record_event(self.student, CHECK_IN, actor=self.staff, source="kiosk")

    def test_temporary_failure_retries_with_backoff_then_fails(self):
        with mock.patch("app1.notifications.worker.deliver", side_effect=smtplib.SMTPServerDisconnected("gone")):
            counts = worker.process_batch("w1")
        self.assertEqual(counts["retry"], 2)
        job = OutboundEmail.objects.first()
        self.assertEqual((job.status, job.attempts), (Status.QUEUED, 1))
        self.assertGreater(job.next_attempt_at, timezone.now())
        self.assertIn("SMTPServerDisconnected", job.last_error)
        OutboundEmail.objects.update(attempts=4, next_attempt_at=timezone.now())
        with mock.patch("app1.notifications.worker.deliver", side_effect=TimeoutError("timed out")):
            worker.process_batch("w1")
        self.assertEqual(set(OutboundEmail.objects.values_list("status", flat=True)), {Status.FAILED})

    def test_permanent_rejection_fails_immediately(self):
        refused = smtplib.SMTPRecipientsRefused({"guardian@family.example": (550, b"no such user")})
        with mock.patch("app1.notifications.worker.deliver", side_effect=refused):
            counts = worker.process_batch("w1")
        self.assertEqual(counts["failed"], 2)
        self.assertEqual(OutboundEmail.objects.filter(status=Status.FAILED, attempts=1).count(), 2)

    def test_connection_failure_retries_everything(self):
        with mock.patch("django.core.mail.backends.locmem.EmailBackend.open", side_effect=OSError("refused")):
            counts = worker.process_batch("w1")
        self.assertEqual(counts["retry"], 2)

    @override_settings(EMAIL_HOST_PASSWORD="super-secret-value")
    def test_errors_never_store_the_password(self):
        with mock.patch(
            "app1.notifications.worker.deliver",
            side_effect=smtplib.SMTPAuthenticationError(535, b"bad super-secret-value"),
        ):
            worker.process_batch("w1")
        self.assertNotIn("super-secret-value", "".join(OutboundEmail.objects.values_list("last_error", flat=True)))

    def test_crashed_worker_jobs_are_recovered(self):
        old = timezone.now() - dt.timedelta(hours=1)
        OutboundEmail.objects.update(status=Status.PROCESSING, locked_at=old, locked_by="dead", attempts=1)
        requeued, failed = worker.recover_stale()
        self.assertEqual((requeued, failed), (2, 0))
        self.assertIn("may have been sent already", OutboundEmail.objects.first().last_error)
        OutboundEmail.objects.update(status=Status.PROCESSING, locked_at=old, attempts=5)
        self.assertEqual(worker.recover_stale(), (0, 2))

    def test_invalidated_recipient_is_cancelled_at_send_time(self):
        StaffNotificationPreference.objects.filter(user=self.staff).update(receive_check_in=False)
        counts = worker.process_batch("w1")
        self.assertEqual((counts["sent"], counts["cancelled"]), (1, 1))

    def test_address_change_cancels_queued_jobs_immediately(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.student.guardian_email = "new@family.example"
            self.student.save()
        job = OutboundEmail.objects.get(recipient_role="guardian")
        self.assertEqual(job.status, Status.CANCELLED)
        self.assertIn("changed", job.cancelled_reason)

    def test_disabling_notifications_cancels_queue(self):
        with self.captureOnCommitCallbacks(execute=True):
            enable(notifications_enabled=False)
        self.assertEqual(set(OutboundEmail.objects.values_list("status", flat=True)), {Status.CANCELLED})

    def test_staff_retry_and_cancel(self):
        admin = self.login(self.admin_user())
        job = OutboundEmail.objects.get(recipient_role="guardian")
        OutboundEmail.objects.filter(pk=job.pk).update(status=Status.FAILED, attempts=5)
        self.client.post(reverse("notification-retry", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts), (Status.QUEUED, 0))
        self.assertTrue(AuditEvent.objects.filter(action="notification_retried", actor=admin).exists())
        self.client.post(reverse("notification-cancel", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.status, Status.CANCELLED)

    def test_retry_refused_when_recipient_no_longer_valid(self):
        self.login(self.admin_user())
        job = OutboundEmail.objects.get(recipient_role="guardian")
        OutboundEmail.objects.filter(pk=job.pk).update(status=Status.FAILED)
        type(self.student).objects.filter(pk=self.student.pk).update(notify_guardian=False)
        self.client.post(reverse("notification-retry", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.status, Status.FAILED)

    def test_heartbeat_and_healthcheck(self):
        self.assertTrue(worker.worker_is_stale())
        worker.beat("w1", 3)
        self.assertFalse(worker.worker_is_stale())


class DigestAndVerificationTests(AppTestCase):
    def setUp(self):
        super().setUp()
        enable(digest_enabled=True)
        self.staff = verified_staff("office", receive_digest=True)

    def test_digest_once_per_address_per_day(self):
        self.assertEqual(digest.enqueue_digests(), 1)
        self.assertEqual(digest.enqueue_digests(), 0)
        job = OutboundEmail.objects.get(kind="digest")
        self.assertIn("Students on the register", job.body_text)
        enable(digest_enabled=False)
        self.assertEqual(digest.enqueue_digests(timezone.localdate() + dt.timedelta(days=1)), 0)

    def test_verification_flow(self):
        student = make_student()
        self.assertTrue(verification.request("student", student, self.staff))
        job = OutboundEmail.objects.get(kind="verification")
        link = job.body_text.split("https://attendance.example.test", 1)[1].split()[0]
        self.assertEqual(self.client.get(link).status_code, 200)
        student.refresh_from_db()
        self.assertFalse(student.student_email_is_verified)  # GET alone never verifies
        self.client.post(link)
        student.refresh_from_db()
        self.assertTrue(student.student_email_is_verified)

    def test_verification_rejects_changed_address_and_bad_tokens(self):
        student = make_student()
        verification.request("student", student, self.staff)
        link = (
            OutboundEmail.objects.get(kind="verification")
            .body_text.split("https://attendance.example.test", 1)[1]
            .split()[0]
        )
        student.email = "changed@students.example"
        student.save()
        self.assertEqual(self.client.post(link).status_code, 400)
        self.assertEqual(self.client.get("/verify-email/not-a-token/").status_code, 400)


class TestEmailEndpointTests(AppTestCase):
    def test_admin_only_rate_limited_and_local(self):
        teacher = self.instructor()
        self.login(teacher)
        self.assertEqual(self.client.post(reverse("test-email")).status_code, 403)
        admin = self.admin_user()
        admin.email = "admin@institute.example"
        admin.save()
        self.login(admin)
        self.client.post(reverse("test-email"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["admin@institute.example"])
        self.client.post(reverse("test-email"))
        self.assertEqual(len(mail.outbox), 1)

    def test_password_reset_mail_and_throttle(self):
        user = make_user("resetme")
        user.email = "resetme@institute.example"
        user.save()
        self.client.post(reverse("password_reset"), {"email": user.email})
        self.client.post(reverse("password_reset"), {"email": user.email})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/password-reset/", mail.outbox[0].body)


@tag("concurrency")
class ConcurrentWorkerTests(TransactionTestCase):
    """Two workers must never claim the same job (SKIP LOCKED on PostgreSQL)."""

    def test_parallel_claims_are_disjoint(self):
        enable()
        for index in range(40):
            OutboundEmail.objects.create(
                kind="check_in",
                recipient_role="staff",
                recipient_email=f"r{index}@x.example",
                dedupe_key=f"k{index}",
                subject="s",
                body_text="b",
            )
        claimed, errors = {}, []
        barrier = threading.Barrier(4)

        def run(name):
            try:
                barrier.wait(timeout=10)
                claimed[name] = [job.pk for job in worker.claim(name, limit=15)]
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(f"w{i}",)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(errors, [])
        all_ids = [pk for ids in claimed.values() for pk in ids]
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertEqual(len(all_ids), 40)
        self.assertEqual(OutboundEmail.objects.filter(status=Status.PROCESSING).count(), 40)

    def test_parallel_digest_runs_create_one_message(self):
        enable(digest_enabled=True)
        verified_staff("office", receive_digest=True)
        results, barrier = [], threading.Barrier(4)

        def run():
            barrier.wait(timeout=10)
            results.append(digest.enqueue_digests())
            connection.close()

        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(sum(results), 1)
        self.assertEqual(OutboundEmail.objects.filter(kind="digest").count(), 1)
