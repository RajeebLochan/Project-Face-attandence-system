import uuid
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .storage import private_storage

BRANCH_CHOICES = [
    ("CSE", "Computer Science (CSE)"),
    ("ETC", "Electronics and Telecommunication (ETC)"),
    ("EE", "Electrical Engineering (EE)"),
    ("ME", "Mechanical Engineering (ME)"),
    ("A&EI", "Applied Electronics & Instrumentation (A&EI)"),
    ("CIVIL", "Civil Engineering"),
    ("IT", "Information Technology (IT)"),
    ("CHE", "Chemical Engineering"),
]

YEAR_CHOICES = [
    ("1st Year", "First year"),
    ("2nd Year", "Second year"),
    ("3rd Year", "Third year"),
    ("4th Year", "Fourth year"),
]


def student_photo_path(instance, filename):
    """Random, non-guessable file name; never derived from personal data."""
    return f"students/{uuid.uuid4().hex}.jpg"


class Student(models.Model):
    class Consent(models.TextChoices):
        NOT_RECORDED = "not_recorded", "Not recorded"
        GIVEN = "given", "Given"
        WITHDRAWN = "withdrawn", "Withdrawn"

    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=255)
    registration_number = models.CharField(max_length=15, unique=True)
    branch = models.CharField(max_length=100, choices=BRANCH_CHOICES)
    year = models.CharField(max_length=16, choices=YEAR_CHOICES)
    # Stored in PRIVATE_MEDIA_ROOT; served only by a permission-checked view.
    image = models.ImageField(upload_to=student_photo_path, storage=private_storage, blank=True)

    authorized = models.BooleanField(default=False)
    authorized_at = models.DateTimeField(null=True, blank=True)
    authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    consent_status = models.CharField(max_length=20, choices=Consent.choices, default=Consent.NOT_RECORDED)
    consent_recorded_at = models.DateTimeField(null=True, blank=True)
    consent_notice_version = models.CharField(max_length=32, blank=True)
    consent_recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    # Attendance eligibility is independent of biometric consent: a student is on the
    # register from enrolled_on until the day before left_on.
    enrolled_on = models.DateField(default=timezone.localdate)
    left_on = models.DateField(null=True, blank=True, help_text="First day the student is no longer active")

    # Contacts for attendance e-mails. An address only receives mail once it has been
    # verified (the verified_* field holds the exact address that was confirmed).
    guardian_name = models.CharField(max_length=255, blank=True)
    guardian_email = models.EmailField(max_length=255, blank=True)
    notify_student = models.BooleanField(default=False)
    notify_guardian = models.BooleanField(default=False)
    student_email_verified = models.EmailField(max_length=255, blank=True)
    student_email_verified_at = models.DateTimeField(null=True, blank=True)
    guardian_email_verified = models.EmailField(max_length=255, blank=True)
    guardian_email_verified_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]
        permissions = [
            ("authorize_student", "Can authorize students for face recognition"),
            ("view_student_photo", "Can view enrolment photos"),
            ("import_students", "Can import students from CSV"),
        ]
        indexes = [
            models.Index(fields=["authorized", "consent_status"], name="student_eligibility_idx"),
        ]

    def __str__(self):
        # Used by the admin and forms. Includes the registration number so that two
        # students who share a name can always be told apart.
        return f"{self.name} ({self.registration_number})"

    @property
    def has_consent(self):
        return self.consent_status == self.Consent.GIVEN

    @property
    def is_recognition_eligible(self):
        return self.authorized and self.has_consent and bool(self.image)

    def is_active_on(self, day):
        return self.enrolled_on <= day and (self.left_on is None or day < self.left_on)

    @property
    def is_active(self):
        return self.is_active_on(timezone.localdate())

    @property
    def student_email_is_verified(self):
        return bool(self.email) and self.student_email_verified.lower() == self.email.lower()

    @property
    def guardian_email_is_verified(self):
        return bool(self.guardian_email) and self.guardian_email_verified.lower() == self.guardian_email.lower()


class FaceEmbedding(models.Model):
    """One face template per student per model version. Never exposed through views."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="embeddings")
    model_version = models.CharField(max_length=64)
    vector = models.BinaryField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["student", "model_version"], name="uniq_embedding_student_model"),
        ]

    def __str__(self):
        return f"Embedding #{self.pk} for student #{self.student_id} ({self.model_version})"


class Attendance(models.Model):
    # PROTECT: attendance history must not disappear silently when a student is deleted.
    student = models.ForeignKey(Student, on_delete=models.PROTECT)
    date = models.DateField()
    check_in_time = models.DateTimeField(null=True, blank=True)
    check_out_time = models.DateTimeField(null=True, blank=True)
    # An unfinished day (check-in without check-out) is never closed with an invented time.
    # Staff either correct it with the real time or mark it reviewed, which keeps it open.
    checkout_review_at = models.DateTimeField(null=True, blank=True)
    checkout_review_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "student__name", "pk"]
        permissions = [("use_kiosk", "Can use the recognition kiosk")]
        constraints = [
            models.UniqueConstraint(fields=["student", "date"], name="uniq_attendance_student_date"),
            models.CheckConstraint(
                condition=Q(check_out_time__isnull=True) | Q(check_in_time__isnull=False),
                name="attendance_checkout_requires_checkin",
            ),
        ]
        indexes = [models.Index(fields=["date"], name="attendance_date_idx")]

    def __str__(self):
        return f"Attendance #{self.pk} student #{self.student_id} on {self.date}"

    def duration(self):
        if self.check_in_time and self.check_out_time:
            return self.check_out_time - self.check_in_time
        return None

    def calculate_duration(self):
        duration = self.duration()
        if duration is None:
            return ""
        total = int(max(duration, timedelta(0)).total_seconds()) // 60
        hours, minutes = divmod(total, 60)
        return f"{hours}h {minutes:02d}m"


class AuditEvent(models.Model):
    """Append-only accountability log. Never stores images, embeddings or contact details."""

    class Action(models.TextChoices):
        ENROLLED = "enrolled", "Student enrolled"
        DETAILS_CHANGED = "details_changed", "Student details changed"
        PHOTO_REPLACED = "photo_replaced", "Enrolment photo replaced"
        AUTHORIZED = "authorized", "Authorized for recognition"
        AUTHORIZATION_REVOKED = "authorization_revoked", "Authorization revoked"
        BIOMETRICS_REMOVED = "biometrics_removed", "Biometric data removed / consent withdrawn"
        STUDENT_DELETED = "student_deleted", "Student deleted"
        STUDENTS_IMPORTED = "students_imported", "Students imported"
        STATUS_CHANGED = "status_changed", "Student active status changed"
        CONTACTS_CHANGED = "contacts_changed", "Contacts or notification preferences changed"
        EMAIL_VERIFIED = "email_verified", "E-mail address verified"
        BATCH_CHANGED = "batch_changed", "Batch or roster changed"
        SCHEDULE_CHANGED = "schedule_changed", "Schedule changed"
        HOLIDAY_CHANGED = "holiday_changed", "Holiday changed"
        LEAVE_RECORDED = "leave_recorded", "Leave recorded"
        LEAVE_CANCELLED = "leave_cancelled", "Leave cancelled"
        CHECKOUT_REVIEWED = "checkout_reviewed", "Missing check-out reviewed"
        SETTINGS_CHANGED = "settings_changed", "Settings changed"
        STAFF_CHANGED = "staff_changed", "Staff account changed"
        NOTIFICATION_RETRIED = "notification_retried", "Notification retried"
        NOTIFICATION_CANCELLED = "notification_cancelled", "Notification cancelled"
        TEST_EMAIL_SENT = "test_email_sent", "Test e-mail sent"
        CHECK_IN = "check_in", "Checked in"
        CHECK_OUT = "check_out", "Checked out"
        ATTENDANCE_CORRECTED = "attendance_corrected", "Attendance corrected"

    class Source(models.TextChoices):
        KIOSK = "kiosk", "Recognition kiosk (staff confirmed)"
        MANUAL = "manual", "Manual entry"
        STAFF = "staff", "Staff action"
        SYSTEM = "system", "System"

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.STAFF)
    student = models.ForeignKey(Student, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events")
    attendance = models.ForeignKey(
        Attendance, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events"
    )
    reason = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.get_action_display()} at {self.created_at:%Y-%m-%d %H:%M}"


# --- Operations: institution settings, batches, schedules, leave ------------------------


class InstitutionSettings(models.Model):
    """Single row (pk=1) of institution details and notification switches."""

    name = models.CharField("Institution name", max_length=200, blank=True)
    contact_email = models.EmailField("Contact e-mail for attendance questions", blank=True)
    contact_phone = models.CharField("Contact phone", max_length=40, blank=True)
    notifications_enabled = models.BooleanField(default=False)
    notify_check_in = models.BooleanField("Send check-in e-mails", default=True)
    notify_check_out = models.BooleanField("Send check-out e-mails", default=True)
    notify_corrections = models.BooleanField("Send e-mails for corrections and past entries", default=False)
    digest_enabled = models.BooleanField("Send a daily summary to selected staff", default=False)
    digest_time = models.TimeField("Daily summary time", default=time(18, 0))
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name_plural = "institution settings"
        permissions = [
            ("manage_settings", "Can change institution and notification settings"),
            ("manage_staff", "Can manage staff accounts"),
            ("manage_notifications", "Can view, retry and cancel notification e-mails"),
        ]

    def __str__(self):
        return self.name or "Institution settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Batch(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True, help_text="Short code used in CSV imports, e.g. CSE-2026-A")
    is_active = models.BooleanField(default=True)
    instructors = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="assigned_batches")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "batches"
        permissions = [
            ("view_all_batches", "Can see students and attendance in every batch"),
            ("manage_batches", "Can manage batches, rosters, schedules and holidays"),
        ]

    def __str__(self):
        return self.name

    def schedule_on(self, day):
        return self.schedules.filter(effective_from__lte=day).order_by("-effective_from").first()


class BatchMembership(models.Model):
    """Effective-dated roster entry: the student belongs to the batch from start_date until
    the day before end_date. Past rows are never rewritten; moves close one row and open
    another, so historical reports keep the roster that applied at the time."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="memberships")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="memberships")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-start_date", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gt=models.F("start_date")),
                name="membership_end_after_start",
            ),
        ]
        indexes = [models.Index(fields=["student", "start_date"], name="membership_student_idx")]

    def __str__(self):
        return f"Student #{self.student_id} in {self.batch} from {self.start_date}"

    def covers(self, day):
        return self.start_date <= day and (self.end_date is None or day < self.end_date)


WEEKDAY_CHOICES = [("0", "Mon"), ("1", "Tue"), ("2", "Wed"), ("3", "Thu"), ("4", "Fri"), ("5", "Sat"), ("6", "Sun")]


class BatchSchedule(models.Model):
    """Working days and expected start for a batch, effective from a date. A new schedule
    adds a row; earlier rows stay so past days keep the rules that applied then."""

    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="schedules")
    effective_from = models.DateField()
    weekdays = models.CharField(max_length=13, default="0,1,2,3,4", help_text="Comma-separated, Monday = 0")
    start_time = models.TimeField()
    grace_minutes = models.PositiveSmallIntegerField(default=10)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "effective_from"], name="uniq_schedule_batch_date"),
        ]

    def __str__(self):
        return f"{self.batch} from {self.effective_from}"

    @property
    def weekday_set(self):
        return {int(day) for day in self.weekdays.split(",") if day.strip().isdigit()}

    def weekday_labels(self):
        labels = dict(WEEKDAY_CHOICES)
        return ", ".join(labels[str(day)] for day in sorted(self.weekday_set))

    def late_after(self, day):
        """Aware datetime after which a check-in on ``day`` counts as late."""
        start = timezone.make_aware(datetime.combine(day, self.start_time), timezone.get_current_timezone())
        return start + timedelta(minutes=self.grace_minutes)


class Holiday(models.Model):
    date = models.DateField()
    name = models.CharField(max_length=120)
    batch = models.ForeignKey(
        Batch,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="holidays",
        help_text="Leave empty for an institution-wide holiday",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["date", "batch"], name="uniq_holiday_batch_date"),
            models.UniqueConstraint(fields=["date"], condition=Q(batch__isnull=True), name="uniq_holiday_global_date"),
        ]

    def __str__(self):
        return f"{self.name} ({self.date})"


class Leave(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="leaves")
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.CharField(max_length=300)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-start_date", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=models.F("start_date")), name="leave_end_after_start"),
        ]

    def __str__(self):
        return f"Leave for student #{self.student_id} {self.start_date}–{self.end_date}"


# --- Notifications ----------------------------------------------------------------------


class StaffNotificationPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preference"
    )
    receive_check_in = models.BooleanField(default=False)
    receive_check_out = models.BooleanField(default=False)
    receive_corrections = models.BooleanField(default=False)
    receive_digest = models.BooleanField(default=False)
    verified_email = models.EmailField(max_length=254, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Notification preferences for user #{self.user_id}"

    @property
    def email_is_verified(self):
        return bool(self.user.email) and self.verified_email.lower() == self.user.email.lower()

    @property
    def receives_anything(self):
        return self.receive_check_in or self.receive_check_out or self.receive_corrections or self.receive_digest


class OutboundEmail(models.Model):
    """Durable outbox row, created in the same transaction as the event it reports.

    A background worker sends committed rows. "Sent" means the mail server accepted the
    message; it does not prove inbox delivery."""

    class Kind(models.TextChoices):
        CHECK_IN = "check_in", "Check-in"
        CHECK_OUT = "check_out", "Check-out"
        CORRECTION = "correction", "Correction or past entry"
        DIGEST = "digest", "Daily summary"
        VERIFICATION = "verification", "Address verification"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        SENT = "sent", "Accepted by mail server"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class Role(models.TextChoices):
        STAFF = "staff", "Staff"
        STUDENT = "student", "Student"
        GUARDIAN = "guardian", "Parent or guardian"

    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    recipient_role = models.CharField(max_length=10, choices=Role.choices)
    recipient_email = models.EmailField(max_length=254)
    recipient_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    student = models.ForeignKey(Student, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    audit_event = models.ForeignKey(AuditEvent, null=True, blank=True, on_delete=models.SET_NULL, related_name="emails")
    dedupe_key = models.CharField(max_length=200, unique=True)
    subject = models.CharField(max_length=255)
    body_text = models.TextField()
    body_html = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=80, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["audit_event", "recipient_email", "kind"],
                condition=Q(audit_event__isnull=False),
                name="uniq_email_event_recipient_kind",
            ),
        ]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="outbox_due_idx")]

    def __str__(self):
        return f"{self.get_kind_display()} e-mail #{self.pk} ({self.get_status_display()})"


class WorkerHeartbeat(models.Model):
    name = models.CharField(max_length=80, primary_key=True)
    last_seen = models.DateTimeField()
    processed = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.name} last seen {self.last_seen:%Y-%m-%d %H:%M}"
