"""E-mail address verification. A recipient only gets attendance e-mails after opening a
signed link sent to that exact address and confirming it (POST, so link scanners that
fetch URLs automatically cannot verify an address)."""

import time

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from ..models import AuditEvent, InstitutionSettings, OutboundEmail, StaffNotificationPreference, Student
from .core import hashed, safe_create

SALT = "app1.email-verification"
MAX_AGE_SECONDS = 7 * 24 * 3600
Role = OutboundEmail.Role


class VerificationError(Exception):
    pass


def _target(role, obj):
    if role == Role.STAFF:
        return obj.email
    if role == Role.STUDENT:
        return obj.email
    return obj.guardian_email


def _display_name(role, obj):
    if role == Role.STAFF:
        return obj.get_full_name() or obj.get_username()
    if role == Role.STUDENT:
        return obj.name
    return obj.guardian_name or "Parent or guardian"


def request(role, obj, actor):
    """Queue a verification e-mail for a staff user, a student or a student's guardian."""
    address = (_target(role, obj) or "").strip().lower()
    if not address:
        raise VerificationError("There is no e-mail address to verify.")
    if not settings.SITE_URL:
        raise VerificationError("SITE_URL is not configured, so a verification link cannot be built.")
    token = signing.dumps({"r": role, "id": obj.pk, "e": address}, salt=SALT)
    context = {
        "institution": InstitutionSettings.load(),
        "link": f"{settings.SITE_URL}{reverse('verify-email', args=[token])}",
        "role": role,
        "name": _display_name(role, obj),
        "student": obj if role != Role.STAFF else None,
        "valid_days": MAX_AGE_SECONDS // 86400,
    }
    window = int(time.time() // 600)  # at most one request per address per 10 minutes
    job, created = safe_create(
        dedupe_key=f"verify:{role}:{obj.pk}:{hashed(address)}:{window}",
        kind=OutboundEmail.Kind.VERIFICATION,
        recipient_role=role,
        recipient_email=address,
        recipient_user=obj if role == Role.STAFF else None,
        student=obj if role != Role.STAFF else None,
        subject="Confirm your e-mail address for attendance notifications",
        body_text=render_to_string("emails/verification.txt", context).strip() + "\n",
        body_html=render_to_string("emails/verification.html", context),
    )
    return created


def read(token):
    try:
        data = signing.loads(token, salt=SALT, max_age=MAX_AGE_SECONDS)
    except signing.SignatureExpired:
        raise VerificationError("This link has expired. Ask the institution to send a new one.") from None
    except signing.BadSignature:
        raise VerificationError("This link is not valid.") from None
    if data.get("r") not in Role.values:
        raise VerificationError("This link is not valid.")
    return data


def confirm(token):
    data = read(token)
    role, pk, address = data["r"], data["id"], data["e"]
    now = timezone.now()
    with transaction.atomic():
        if role == Role.STAFF:
            from django.contrib.auth import get_user_model

            user = get_user_model().objects.filter(pk=pk, is_active=True).first()
            if user is None or user.email.lower() != address:
                raise VerificationError("This address is no longer used for this account.")
            pref, _ = StaffNotificationPreference.objects.get_or_create(user=user)
            pref.verified_email, pref.verified_at = address, now
            pref.save()
            student = None
        else:
            student = Student.objects.select_for_update().filter(pk=pk).first()
            current = (student.email if role == Role.STUDENT else student.guardian_email) if student else ""
            if student is None or current.lower() != address:
                raise VerificationError("This address is no longer on record.")
            if role == Role.STUDENT:
                student.student_email_verified, student.student_email_verified_at = address, now
            else:
                student.guardian_email_verified, student.guardian_email_verified_at = address, now
            student.save()
        AuditEvent.objects.create(
            action=AuditEvent.Action.EMAIL_VERIFIED,
            source=AuditEvent.Source.SYSTEM,
            student=student,
            details={"role": role, "user_id": pk if role == Role.STAFF else None},
        )
    return role
