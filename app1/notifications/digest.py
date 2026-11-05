"""Optional daily summary for selected staff. One e-mail per address per day: the outbox
dedupe key makes repeated scheduler runs (or two schedulers) harmless."""

from datetime import datetime

from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from ..models import Attendance, InstitutionSettings, OutboundEmail, StaffNotificationPreference
from ..scope import attendance_for, sees_all, students_for
from ..services.register import LABELS, statuses_for_day
from .core import hashed, kind_enabled, safe_create

Kind = OutboundEmail.Kind


def is_due(config, now=None):
    now = timezone.localtime(now or timezone.now())
    return now.time() >= config.digest_time


def build_context(user, day, config):
    students = students_for(user, on=day).filter(enrolled_on__lte=day)
    counts = {}
    for status in statuses_for_day(students, day):
        counts[status.status] = counts.get(status.status, 0) + 1
    open_records = (
        attendance_for(user)
        .filter(date__lt=day, check_in_time__isnull=False, check_out_time__isnull=True, checkout_review_at__isnull=True)
        .count()
    )
    failures = OutboundEmail.objects.filter(status=OutboundEmail.Status.FAILED).count() if sees_all(user) else None
    return {
        "institution": config,
        "day_label": f"{day:%A} {day.day} {day:%B %Y}",
        "rows": [(LABELS[key], counts[key]) for key in LABELS if counts.get(key)],
        "total": sum(counts.values()),
        "open_records": open_records,
        "failures": failures,
        "dashboard_url": f"{settings.SITE_URL}{reverse('home')}" if settings.SITE_URL else "",
        "recorded_today": Attendance.objects.filter(date=day).count(),
    }


def enqueue_digests(day=None, now=None):
    config = InstitutionSettings.load()
    if not kind_enabled(config, Kind.DIGEST):
        return 0
    day = day or timezone.localdate(now or timezone.now())
    created = 0
    prefs = StaffNotificationPreference.objects.select_related("user").filter(receive_digest=True, user__is_active=True)
    for pref in prefs:
        if not pref.email_is_verified:
            continue
        context = build_context(pref.user, day, config)
        _, made = safe_create(
            dedupe_key=f"digest:{day.isoformat()}:{hashed(pref.user.email)}",
            kind=Kind.DIGEST,
            recipient_role=OutboundEmail.Role.STAFF,
            recipient_email=pref.user.email.lower(),
            recipient_user=pref.user,
            subject=f"Attendance summary for {context['day_label']}",
            body_text=render_to_string("emails/digest.txt", context).strip() + "\n",
            body_html=render_to_string("emails/digest.html", context),
        )
        created += int(made)
    return created


def parse_day(value):
    return datetime.strptime(value, "%Y-%m-%d").date()
