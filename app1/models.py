import uuid
from datetime import timedelta

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

    consent_status = models.CharField(
        max_length=20, choices=Consent.choices, default=Consent.NOT_RECORDED
    )
    consent_recorded_at = models.DateTimeField(null=True, blank=True)
    consent_notice_version = models.CharField(max_length=32, blank=True)
    consent_recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]
        permissions = [
            ("authorize_student", "Can authorize students for face recognition"),
            ("view_student_photo", "Can view enrolment photos"),
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


class FaceEmbedding(models.Model):
    """One face template per student per model version. Never exposed through views."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="embeddings")
    model_version = models.CharField(max_length=64)
    vector = models.BinaryField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "model_version"], name="uniq_embedding_student_model"
            ),
        ]

    def __str__(self):
        return f"Embedding #{self.pk} for student #{self.student_id} ({self.model_version})"


class Attendance(models.Model):
    # PROTECT: attendance history must not disappear silently when a student is deleted.
    student = models.ForeignKey(Student, on_delete=models.PROTECT)
    date = models.DateField()
    check_in_time = models.DateTimeField(null=True, blank=True)
    check_out_time = models.DateTimeField(null=True, blank=True)
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
        total = int(max(duration, timedelta(0)).total_seconds())
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}h {minutes}m {seconds}s"


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
    student = models.ForeignKey(
        Student, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events"
    )
    attendance = models.ForeignKey(
        Attendance, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events"
    )
    reason = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.get_action_display()} at {self.created_at:%Y-%m-%d %H:%M}"
