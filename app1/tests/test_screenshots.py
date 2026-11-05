"""Capture documentation screenshots with synthetic data (tagged "screenshots").

Run:  SCREENSHOT_DIR=docs/screenshots python manage.py test --tag screenshots

Every name, number and photo is synthetic: photos are drawn silhouettes, not faces,
and recognition uses the fake engine. Skipped unless SCREENSHOT_DIR is set.
"""

import datetime as dt
import os
import random
import unittest
from io import BytesIO
from pathlib import Path

import numpy as np
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.files.base import ContentFile
from django.test import override_settings, tag
from django.utils import timezone
from PIL import Image, ImageDraw

from app1.models import (
    Attendance,
    AuditEvent,
    Batch,
    BatchMembership,
    BatchSchedule,
    FaceEmbedding,
    Holiday,
    InstitutionSettings,
    Leave,
    OutboundEmail,
    StaffNotificationPreference,
    Student,
    student_photo_path,
)
from app1.recognition.engine import DetectedFace, reset_engine
from app1.recognition.gallery import gallery
from app1.storage import PrivateMediaStorage

from .fakes import FakeFaceEngine
from .helpers import make_user

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

OUT = os.environ.get("SCREENSHOT_DIR")
NAMES = [
    "Ananya Rao",
    "Rohan Mehta",
    "Priya Nair",
    "Arjun Das",
    "Kavya Iyer",
    "Vikram Singh",
    "Sneha Pillai",
    "Aditya Kulkarni",
    "Meera Joshi",
    "Karan Patel",
    "Isha Reddy",
    "Nikhil Bose",
    "Tanvi Shah",
    "Rahul Menon",
    "Diya Ghosh",
    "Siddharth Jain",
    "Pooja Hegde",
    "Manav Arora",
    "Riya Sen",
    "Kabir Malhotra",
]


def silhouette(seed):
    """A drawn head-and-shoulders silhouette. Not a face; no biometric content."""
    rng = random.Random(seed)
    tone = 196 + rng.randint(-12, 12)
    image = Image.new("RGB", (300, 400), (tone, tone - 4, tone - 10))
    draw = ImageDraw.Draw(image)
    shade = (118 + rng.randint(-20, 20), 120, 128)
    draw.ellipse((95, 70, 205, 200), fill=shade)
    draw.ellipse((40, 220, 260, 470), fill=shade)
    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=88)
    return buffer.getvalue()


def demo_vector(index):
    vector = np.zeros(128, dtype=np.float32)
    vector[64 + index] = 1.0
    return vector


def at(day, hour, minute):
    return timezone.make_aware(dt.datetime.combine(day, dt.time(hour, minute)))


def seed():
    today = timezone.localdate()
    config = InstitutionSettings.load()
    config.name, config.contact_email, config.contact_phone = (
        "Northfield Institute of Technology",
        "attendance@northfield.example.edu",
        "+91 80 4000 1200",
    )
    config.notifications_enabled = True
    config.save()
    admin = make_user("asha.admin", group="Attendance admin")
    admin.first_name, admin.last_name, admin.email = "Asha", "Kulkarni", "asha.kulkarni@northfield.example.edu"
    admin.save()
    teacher = make_user("rahul.menon", group="Instructor")
    teacher.first_name, teacher.last_name, teacher.email = "Rahul", "Menon", "rahul.menon@northfield.example.edu"
    teacher.save()
    StaffNotificationPreference.objects.create(
        user=admin, receive_check_in=False, receive_digest=True, verified_email=admin.email, verified_at=timezone.now()
    )
    batch_a = Batch.objects.create(name="CSE 2026 · Section A", code="CSE-26-A")
    batch_b = Batch.objects.create(name="ETC 2026 · Section B", code="ETC-26-B")
    batch_a.instructors.add(teacher)
    start = today - dt.timedelta(days=60)
    BatchSchedule.objects.create(
        batch=batch_a, effective_from=start, weekdays="0,1,2,3,4", start_time=dt.time(9, 0), grace_minutes=10
    )
    BatchSchedule.objects.create(
        batch=batch_b, effective_from=start, weekdays="0,1,2,3,4,5", start_time=dt.time(9, 30), grace_minutes=5
    )
    Holiday.objects.create(date=today + dt.timedelta(days=9), name="Founders’ Day")
    rng = random.Random(7)
    storage = PrivateMediaStorage()
    students = []
    for index, name in enumerate(NAMES):
        batch = batch_a if index < 12 else batch_b
        student = Student(
            name=name,
            email=f"{name.split()[0].lower()}.{index}@students.example.edu",
            registration_number=f"{'21CSE' if batch is batch_a else '21ETC'}{101 + index:03d}",
            branch="CSE" if batch is batch_a else "ETC",
            year="2nd Year",
            enrolled_on=start,
            consent_status="given" if index % 6 else "not_recorded",
            authorized=index % 6 not in (0, 5),
        )
        if student.consent_status == "given":
            student.image.name = storage.save(student_photo_path(None, "x.jpg"), ContentFile(silhouette(index)))
        if index == 2:
            student.guardian_name, student.guardian_email = "Suresh Nair", "suresh.nair@example.com"
            student.notify_guardian = True
            student.guardian_email_verified = student.guardian_email
        student.save()
        if student.image:
            FaceEmbedding.objects.create(
                student=student,
                model_version=FakeFaceEngine.model_version,
                vector=demo_vector(index).astype("<f4").tobytes(),
            )
        BatchMembership.objects.create(student=student, batch=batch, start_date=start)
        students.append(student)
    Leave.objects.create(
        student=students[4],
        start_date=today,
        end_date=today + dt.timedelta(days=1),
        reason="Medical appointment",
        recorded_by=admin,
    )
    for back in range(0, 15):
        day = today - dt.timedelta(days=back)
        for index, student in enumerate(students):
            schedule_days = (0, 1, 2, 3, 4) if index < 12 else (0, 1, 2, 3, 4, 5)
            if day.weekday() not in schedule_days or (back == 0 and index == 4) or rng.random() < 0.08:
                continue
            base = (9, 0) if index < 12 else (9, 30)
            minute = base[1] + rng.choice([-12, -8, -5, -3, 0, 2, 4, 6, 9, 14, 22])
            check_in = at(day, base[0] + minute // 60, minute % 60)
            if back == 0:
                if index in (3, 9, 15, 18):
                    continue
                check_out = None if index % 3 else None
            else:
                check_out = (
                    None if (back == 1 and index in (7, 13)) else at(day, 15 + rng.randint(0, 1), rng.randint(0, 59))
                )
            record = Attendance.objects.create(
                student=student, date=day, check_in_time=check_in, check_out_time=check_out
            )
            if back == 0:
                event = AuditEvent.objects.create(
                    actor=teacher if index % 4 else admin,
                    action="check_in",
                    source="kiosk" if index % 4 else "manual",
                    student=student,
                    attendance=record,
                    created_at=check_in,
                )
                if index == 2:
                    OutboundEmail.objects.create(
                        kind="check_in",
                        status="sent",
                        recipient_role="guardian",
                        recipient_email=student.guardian_email,
                        student=student,
                        audit_event=event,
                        dedupe_key=f"demo:{record.pk}",
                        subject=f"Check-in recorded: {student.name}",
                        body_text="Synthetic example message.",
                        attempts=1,
                        sent_at=timezone.now(),
                    )
    OutboundEmail.objects.create(
        kind="check_in",
        status="failed",
        recipient_role="staff",
        recipient_email="rahul.menon@northfield.example.edu",
        recipient_user=teacher,
        student=students[1],
        dedupe_key="demo:failed",
        subject="Check-in recorded: Rohan Mehta",
        body_text="Synthetic example message.",
        attempts=5,
        last_error="SMTPServerDisconnected: Connection unexpectedly closed",
    )
    return admin, teacher, students


@tag("screenshots")
@unittest.skipUnless(OUT and sync_playwright, "set SCREENSHOT_DIR and install Playwright to capture screenshots")
@override_settings(KIOSK_MIN_FRAME_INTERVAL_SECONDS=0.0)
class Screenshots(StaticLiveServerTestCase):
    host = "localhost"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def page_for(self, user, width, height=900):
        self.client.force_login(user)
        context = self.browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=2,
            base_url=self.live_server_url,
            color_scheme="light",
        )
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": self.client.cookies[settings.SESSION_COOKIE_NAME].value,
                    "url": self.live_server_url,
                }
            ]
        )
        return context, context.new_page()

    def shot(self, page, path, name, full=True):
        page.goto(path)
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(Path(OUT) / f"{name}.png"), full_page=full)

    def test_capture(self):
        Path(OUT).mkdir(parents=True, exist_ok=True)
        FakeFaceEngine.reset()
        reset_engine()
        gallery.invalidate()
        admin, teacher, students = seed()
        context, page = self.page_for(admin, 1440)
        self.shot(page, "/", "dashboard")
        self.shot(page, "/students/", "students")
        self.shot(page, f"/students/{students[2].pk}/", "student-detail")
        self.shot(page, f"/attendance/manual/?q=ro&student={students[1].pk}", "manual-attendance")
        self.shot(page, "/reports/", "reports")
        self.shot(page, "/reports/register/", "register")
        self.shot(page, "/notifications/", "notifications")
        self.shot(page, "/settings/", "settings")
        self.shot(page, "/batches/", "batches")
        self.shot(page, "/audit/", "audit")
        self.shot(page, "/staff/", "staff")
        target = students[7]
        FakeFaceEngine.override = lambda image: [
            DetectedFace(box=(10, 10, 150, 150), score=0.99, embedding=demo_vector(7))
        ]
        page.goto("/kiosk/")
        page.click("#kiosk-start")
        page.wait_for_selector("#kiosk-candidate:not([hidden])")
        page.wait_for_timeout(300)
        page.screenshot(path=str(Path(OUT) / "kiosk.png"), full_page=True)
        assert target.name in page.inner_text("#candidate-name")
        page.click("#kiosk-stop")
        context.close()
        context, page = self.page_for(teacher, 390, 844)
        self.shot(page, "/", "mobile-dashboard")
        self.shot(page, f"/attendance/manual/?q=a&student={students[0].pk}", "mobile-manual")
        context.close()
        self.client.logout()
        context = self.browser.new_context(
            viewport={"width": 1440, "height": 900}, device_scale_factor=2, base_url=self.live_server_url
        )
        page = context.new_page()
        self.shot(page, "/login/", "login", full=False)
        context.close()
