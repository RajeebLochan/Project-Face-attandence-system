"""Database-backed delivery worker.

* Claims due jobs with SELECT ... FOR UPDATE SKIP LOCKED on PostgreSQL, so several
  workers never take the same job. On SQLite, IMMEDIATE transactions serialise claims.
* Re-checks each job's recipient right before sending and cancels it if no longer valid.
* Temporary failures are retried with backoff up to NOTIFICATION_MAX_ATTEMPTS; permanent
  rejections (5xx from the server) fail immediately.
* A job left in "processing" by a crashed worker is requeued after
  NOTIFICATION_LEASE_SECONDS. If the crash happened after the server accepted the
  message, the recipient can receive it twice: SMTP gives no exactly-once guarantee.
"""

import logging
import smtplib
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from ..models import OutboundEmail, WorkerHeartbeat
from .core import recipient_still_valid

logger = logging.getLogger(__name__)
Status = OutboundEmail.Status
BACKOFF_SECONDS = (60, 300, 900, 3600, 3 * 3600)


def backoff(attempts):
    return timedelta(seconds=BACKOFF_SECONDS[min(attempts, len(BACKOFF_SECONDS)) - 1])


def is_permanent(exc):
    if isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)):
        return True
    code = getattr(exc, "smtp_code", None)
    return (
        isinstance(exc, smtplib.SMTPResponseException)
        and code is not None
        and 500 <= code < 600
        and not isinstance(exc, smtplib.SMTPAuthenticationError)
    )


def describe(exc):
    text = f"{type(exc).__name__}: {exc}"
    if settings.EMAIL_HOST_PASSWORD:
        text = text.replace(settings.EMAIL_HOST_PASSWORD, "***")
    return text[:500]


def recover_stale(now=None):
    """Requeue (or fail) jobs whose worker stopped while processing them."""
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=settings.NOTIFICATION_LEASE_SECONDS)
    stale = OutboundEmail.objects.filter(status=Status.PROCESSING, locked_at__lt=cutoff)
    failed = stale.filter(attempts__gte=settings.NOTIFICATION_MAX_ATTEMPTS).update(
        status=Status.FAILED,
        locked_at=None,
        locked_by="",
        last_error="Worker stopped while sending; not retried after the last attempt",
    )
    requeued = stale.update(
        status=Status.QUEUED,
        locked_at=None,
        locked_by="",
        next_attempt_at=now,
        last_error="Worker stopped while sending; requeued (the message may have been sent already)",
    )
    return requeued, failed


def claim(worker, limit=20, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        ids = list(
            OutboundEmail.objects.select_for_update(skip_locked=True)
            .filter(status=Status.QUEUED, next_attempt_at__lte=now)
            .order_by("next_attempt_at", "pk")
            .values_list("pk", flat=True)[:limit]
        )
        if ids:
            OutboundEmail.objects.filter(pk__in=ids, status=Status.QUEUED).update(
                status=Status.PROCESSING,
                locked_at=now,
                locked_by=worker,
                attempts=F("attempts") + 1,
            )
    return list(
        OutboundEmail.objects.filter(pk__in=ids, status=Status.PROCESSING, locked_by=worker).select_related(
            "recipient_user", "student", "audit_event__attendance"
        )
    )


def _finish(job, worker, **fields):
    return OutboundEmail.objects.filter(pk=job.pk, status=Status.PROCESSING, locked_by=worker).update(
        locked_at=None, locked_by="", **fields
    )


def deliver(job, connection):
    message = EmailMultiAlternatives(
        subject=f"{settings.EMAIL_SUBJECT_PREFIX}{job.subject}",
        body=job.body_text,
        from_email=settings.DEFAULT_FROM_EMAIL or None,
        to=[job.recipient_email],
        connection=connection,
        headers={"Auto-Submitted": "auto-generated", "X-Auto-Response-Suppress": "All"},
    )
    if job.body_html:
        message.attach_alternative(job.body_html, "text/html")
    message.send(fail_silently=False)


def process_batch(worker, limit=20):
    """Claim and send one batch. Returns a dict of counts."""
    counts = {"sent": 0, "retry": 0, "failed": 0, "cancelled": 0}
    jobs = claim(worker, limit)
    if not jobs:
        return counts
    connection = get_connection(fail_silently=False)
    try:
        connection.open()
    except Exception as exc:  # noqa: BLE001 - connection problems are retried
        for job in jobs:
            _retry_or_fail(job, worker, exc, counts)
        return counts
    try:
        for job in jobs:
            valid, reason = recipient_still_valid(job)
            if not valid:
                counts["cancelled"] += _finish(job, worker, status=Status.CANCELLED, cancelled_reason=reason[:200])
                continue
            try:
                deliver(job, connection)
            except (TimeoutError, smtplib.SMTPException, OSError) as exc:
                if is_permanent(exc):
                    counts["failed"] += _finish(job, worker, status=Status.FAILED, last_error=describe(exc))
                else:
                    _retry_or_fail(job, worker, exc, counts)
            else:
                counts["sent"] += _finish(job, worker, status=Status.SENT, sent_at=timezone.now(), last_error="")
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001 - the messages were already handed over
            logger.warning("Closing the SMTP connection failed", exc_info=True)
    return counts


def _retry_or_fail(job, worker, exc, counts):
    if job.attempts >= settings.NOTIFICATION_MAX_ATTEMPTS:
        counts["failed"] += _finish(job, worker, status=Status.FAILED, last_error=describe(exc))
    else:
        counts["retry"] += _finish(
            job,
            worker,
            status=Status.QUEUED,
            last_error=describe(exc),
            next_attempt_at=timezone.now() + backoff(job.attempts),
        )


def beat(worker, processed=0):
    obj, _ = WorkerHeartbeat.objects.get_or_create(name=worker, defaults={"last_seen": timezone.now()})
    WorkerHeartbeat.objects.filter(pk=obj.pk).update(last_seen=timezone.now(), processed=F("processed") + processed)


def latest_heartbeat():
    return WorkerHeartbeat.objects.order_by("-last_seen").first()


def worker_is_stale(max_age_seconds=None):
    max_age = max_age_seconds or max(60, settings.NOTIFICATION_WORKER_POLL_SECONDS * 6)
    latest = latest_heartbeat()
    return latest is None or latest.last_seen < timezone.now() - timedelta(seconds=max_age)


def retry(job, actor):
    """Staff-initiated retry of a failed or cancelled job (if the recipient is still valid)."""
    valid, reason = recipient_still_valid(job)
    if not valid:
        return False, reason
    updated = OutboundEmail.objects.filter(pk=job.pk, status__in=[Status.FAILED, Status.CANCELLED]).update(
        status=Status.QUEUED,
        attempts=0,
        next_attempt_at=timezone.now(),
        last_error="",
        cancelled_reason="",
    )
    return bool(updated), "" if updated else "Only failed or cancelled messages can be retried"


def cancel(job):
    return OutboundEmail.objects.filter(pk=job.pk, status=Status.QUEUED).update(
        status=Status.CANCELLED, cancelled_reason="Cancelled by staff"
    )
