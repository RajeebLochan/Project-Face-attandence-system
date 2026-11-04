"""Configuration checks, recipient selection, rendering and enqueueing."""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from ..models import AuditEvent, InstitutionSettings, OutboundEmail, StaffNotificationPreference
from ..scope import can_access_student

logger = logging.getLogger(__name__)

Kind = OutboundEmail.Kind
Role = OutboundEmail.Role

PROVIDER_BACKENDS = {"django.core.mail.backends.smtp.EmailBackend"}
LOCAL_BACKENDS = {
    "django.core.mail.backends.locmem.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
    "django.core.mail.backends.console.EmailBackend",
}


def email_transport_problems():
    """Why the mail transport cannot be used, as short human-readable strings."""
    problems = []
    backend = settings.EMAIL_BACKEND
    if backend in PROVIDER_BACKENDS:
        if not settings.EMAIL_HOST:
            problems.append("EMAIL_HOST is not set")
        if not settings.DEFAULT_FROM_EMAIL:
            problems.append("DEFAULT_FROM_EMAIL is not set")
    elif backend not in LOCAL_BACKENDS:
        if backend == "django.core.mail.backends.dummy.EmailBackend":
            problems.append("EMAIL_BACKEND discards all mail")
    return problems


def transport_summary():
    """Non-secret description of the configured transport for the settings page."""
    backend = settings.EMAIL_BACKEND.rsplit(".", 2)[-2] if "." in settings.EMAIL_BACKEND else settings.EMAIL_BACKEND
    security = "SSL/TLS" if settings.EMAIL_USE_SSL else ("STARTTLS" if settings.EMAIL_USE_TLS else "none")
    return {
        "backend": backend,
        "host": settings.EMAIL_HOST or "—",
        "port": settings.EMAIL_PORT,
        "security": security,
        "username_set": bool(settings.EMAIL_HOST_USER),
        "password_set": bool(settings.EMAIL_HOST_PASSWORD),
        "from_address": settings.DEFAULT_FROM_EMAIL or "—",
        "timeout": settings.EMAIL_TIMEOUT,
        "site_url": settings.SITE_URL or "—",
        "is_local": settings.EMAIL_BACKEND in LOCAL_BACKENDS,
    }


def enablement_problems(config=None):
    """Requirements for switching notifications on. Empty list means ready."""
    config = config or InstitutionSettings.load()
    problems = list(email_transport_problems())
    if not config.name:
        problems.append("Institution name is missing")
    if not config.contact_email:
        problems.append("Contact e-mail for attendance questions is missing")
    if not settings.SITE_URL:
        problems.append("SITE_URL is not set (needed for verification links)")
    return problems


def hashed(email):
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]


# --- Which events notify whom ----------------------------------------------------------


def kind_for_event(event):
    """Map an audit event to a notification kind, or None if it never notifies."""
    if event.action in (AuditEvent.Action.CHECK_IN, AuditEvent.Action.CHECK_OUT):
        if event.details.get("past_day"):
            return Kind.CORRECTION
        return Kind.CHECK_IN if event.action == AuditEvent.Action.CHECK_IN else Kind.CHECK_OUT
    if event.action == AuditEvent.Action.ATTENDANCE_CORRECTED:
        return Kind.CORRECTION
    return None


def kind_enabled(config, kind):
    if not config.notifications_enabled:
        return False
    return {
        Kind.CHECK_IN: config.notify_check_in,
        Kind.CHECK_OUT: config.notify_check_out,
        Kind.CORRECTION: config.notify_corrections,
        Kind.DIGEST: config.digest_enabled,
        Kind.VERIFICATION: True,
    }[kind]


def staff_pref_allows(pref, kind):
    return {
        Kind.CHECK_IN: pref.receive_check_in,
        Kind.CHECK_OUT: pref.receive_check_out,
        Kind.CORRECTION: pref.receive_corrections,
        Kind.DIGEST: pref.receive_digest,
    }.get(kind, False)


@dataclass(frozen=True)
class Recipient:
    email: str
    role: str
    user: object = None


def recipients_for(student, kind, day):
    """Verified, permitted recipients, one per distinct address (staff first)."""
    found = {}
    prefs = StaffNotificationPreference.objects.select_related("user").filter(user__is_active=True)
    for pref in prefs:
        if staff_pref_allows(pref, kind) and pref.email_is_verified and can_access_student(pref.user, student, on=day):
            found.setdefault(pref.user.email.lower(), Recipient(pref.user.email.lower(), Role.STAFF, pref.user))
    if student.notify_student and student.student_email_is_verified:
        found.setdefault(student.email.lower(), Recipient(student.email.lower(), Role.STUDENT))
    if student.notify_guardian and student.guardian_email_is_verified:
        found.setdefault(student.guardian_email.lower(), Recipient(student.guardian_email.lower(), Role.GUARDIAN))
    return list(found.values())


def recipient_still_valid(job):
    """Re-check a queued job just before sending (preferences may have changed)."""
    config = InstitutionSettings.load()
    if job.kind != Kind.VERIFICATION and not kind_enabled(config, job.kind):
        return False, "Notifications for this event type are switched off"
    if job.kind == Kind.VERIFICATION:
        return _verification_still_valid(job)
    if job.recipient_role == Role.STAFF:
        user = job.recipient_user
        if user is None or not user.is_active:
            return False, "Staff account is inactive or deleted"
        pref = StaffNotificationPreference.objects.filter(user=user).first()
        if pref is None or not staff_pref_allows(pref, job.kind):
            return False, "Staff member no longer receives this notification"
        if not pref.email_is_verified or user.email.lower() != job.recipient_email:
            return False, "Staff e-mail address changed or is not verified"
        if job.kind != Kind.DIGEST and job.student and not can_access_student(user, job.student, on=_event_day(job)):
            return False, "Staff member no longer has access to this student"
        return True, ""
    student = job.student
    if student is None:
        return False, "Student was deleted"
    if job.recipient_role == Role.STUDENT:
        ok = (
            student.notify_student
            and student.student_email_is_verified
            and student.email.lower() == job.recipient_email
        )
        return (True, "") if ok else (False, "Student e-mail notifications are off or the address changed")
    ok = (
        student.notify_guardian
        and student.guardian_email_is_verified
        and student.guardian_email.lower() == job.recipient_email
    )
    return (True, "") if ok else (False, "Guardian e-mail notifications are off or the address changed")


def _verification_still_valid(job):
    if job.recipient_role == Role.STAFF:
        user = job.recipient_user
        ok = user is not None and user.is_active and user.email.lower() == job.recipient_email
    elif job.student is None:
        ok = False
    elif job.recipient_role == Role.STUDENT:
        ok = job.student.email.lower() == job.recipient_email
    else:
        ok = job.student.guardian_email.lower() == job.recipient_email
    return (True, "") if ok else (False, "The address changed after verification was requested")


def _event_day(job):
    if job.audit_event and job.audit_event.attendance:
        return job.audit_event.attendance.date
    return timezone.localdate()


# --- Rendering -------------------------------------------------------------------------


def format_moment(value):
    local = timezone.localtime(value)
    return {
        "date": f"{local:%A} {local.day} {local:%B %Y}",
        "time": local.strftime("%H:%M"),
        "zone": f"{local.tzname()}, {settings.TIME_ZONE}",
    }


def render_event_email(event, kind, role, config=None):
    config = config or InstitutionSettings.load()
    attendance = event.attendance
    student = event.student
    when = attendance.check_out_time if event.action == AuditEvent.Action.CHECK_OUT else attendance.check_in_time
    context = {
        "institution": config,
        "student": student,
        "kind": kind,
        "event": event,
        "event_label": {
            AuditEvent.Action.CHECK_IN: "Check-in",
            AuditEvent.Action.CHECK_OUT: "Check-out",
        }.get(event.action, "Attendance record corrected"),
        "moment": format_moment(when) if when else None,
        "recorded": format_moment(event.created_at),
        "manual": event.source == AuditEvent.Source.MANUAL,
        "past_entry": bool(event.details.get("backdated")),
        "attendance": attendance,
        "after": _correction_times(event),
        "record_url": (
            f"{settings.SITE_URL}{reverse('student-detail', args=[student.pk])}"
            if role == Role.STAFF and settings.SITE_URL
            else ""
        ),
    }
    subject_name = f"{student.name} ({student.registration_number})"
    subject = {
        Kind.CHECK_IN: f"Check-in recorded: {subject_name}",
        Kind.CHECK_OUT: f"Check-out recorded: {subject_name}",
        Kind.CORRECTION: f"Attendance record updated: {subject_name}",
    }[kind]
    return (
        subject,
        render_to_string("emails/attendance_event.txt", context).strip() + "\n",
        render_to_string("emails/attendance_event.html", context),
    )


def _correction_times(event):
    if event.action != AuditEvent.Action.ATTENDANCE_CORRECTED:
        return None
    after = event.details.get("after", {})
    out = {}
    for key in ("check_in_time", "check_out_time"):
        value = after.get(key)
        out[key] = format_moment(datetime.fromisoformat(value)) if value else None
    return out


# --- Enqueueing ------------------------------------------------------------------------


def enqueue_for_event(event):
    """Create outbox rows for an attendance audit event, inside the caller's transaction.

    Errors here are logged and swallowed (inside a savepoint) so that a notification
    problem can never block or roll back attendance itself."""
    kind = kind_for_event(event)
    if kind is None or event.student is None or event.attendance is None:
        return []
    try:
        with transaction.atomic():
            config = InstitutionSettings.load()
            if not kind_enabled(config, kind):
                return []
            created = []
            for recipient in recipients_for(event.student, kind, event.attendance.date):
                subject, text, html = render_event_email(event, kind, recipient.role, config)
                job, made = OutboundEmail.objects.get_or_create(
                    dedupe_key=f"event:{event.pk}:{kind}:{hashed(recipient.email)}",
                    defaults=dict(
                        kind=kind,
                        recipient_role=recipient.role,
                        recipient_email=recipient.email,
                        recipient_user=recipient.user,
                        student=event.student,
                        audit_event=event,
                        subject=subject,
                        body_text=text,
                        body_html=html,
                    ),
                )
                if made:
                    created.append(job)
            return created
    except Exception:  # noqa: BLE001 - attendance must not fail because of e-mail
        logger.exception("Could not queue notifications for audit event #%s", event.pk)
        return []


def cancel_invalid_queued(jobs=None):
    """Cancel queued jobs whose recipient or permission is no longer valid."""
    jobs = jobs if jobs is not None else OutboundEmail.objects.filter(status=OutboundEmail.Status.QUEUED)
    cancelled = 0
    for job in jobs.filter(status=OutboundEmail.Status.QUEUED).select_related(
        "recipient_user", "student", "audit_event__attendance"
    ):
        valid, reason = recipient_still_valid(job)
        if not valid:
            cancelled += OutboundEmail.objects.filter(pk=job.pk, status=OutboundEmail.Status.QUEUED).update(
                status=OutboundEmail.Status.CANCELLED, cancelled_reason=reason[:200]
            )
    return cancelled


def safe_create(**fields):
    """get_or_create on dedupe_key that tolerates a concurrent duplicate insert."""
    try:
        with transaction.atomic():
            return OutboundEmail.objects.get_or_create(dedupe_key=fields.pop("dedupe_key"), defaults=fields)
    except IntegrityError:
        return None, False
