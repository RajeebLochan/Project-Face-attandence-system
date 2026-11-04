from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ..forms import NotificationFilterForm
from ..models import AuditEvent, InstitutionSettings, OutboundEmail
from ..notifications import verification, worker
from ..notifications.core import email_transport_problems, format_moment
from ..throttle import allow_rate
from .students import perm


@perm("app1.manage_notifications")
@require_GET
def notification_list(request):
    form = NotificationFilterForm(request.GET or None)
    jobs = OutboundEmail.objects.select_related("student", "recipient_user")
    if form.is_valid():
        if form.cleaned_data["status"]:
            jobs = jobs.filter(status=form.cleaned_data["status"])
        if form.cleaned_data["kind"]:
            jobs = jobs.filter(kind=form.cleaned_data["kind"])
    counts = dict(OutboundEmail.objects.values_list("status").annotate(n=Count("pk")))
    page = Paginator(jobs, 50).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "notifications/list.html",
        {
            "form": form,
            "page": page,
            "query": query.urlencode(),
            "counts": [(value, label, counts.get(value, 0)) for value, label in OutboundEmail.Status.choices],
            "heartbeat": worker.latest_heartbeat(),
            "worker_stale": worker.worker_is_stale(),
            "config": InstitutionSettings.load(),
        },
    )


@perm("app1.manage_notifications")
@require_GET
def notification_detail(request, pk):
    job = get_object_or_404(OutboundEmail.objects.select_related("student", "recipient_user", "audit_event"), pk=pk)
    return render(request, "notifications/detail.html", {"job": job})


def _isolated_html(html):
    response = HttpResponse(html)
    # Shown inside a sandboxed iframe on our own page only; no scripts, no remote loads.
    response["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Cache-Control"] = "private, no-store"
    return response


@perm("app1.manage_notifications")
@require_GET
def notification_html(request, pk):
    job = get_object_or_404(OutboundEmail, pk=pk)
    if not job.body_html:
        raise Http404
    return _isolated_html(job.body_html)


@perm("app1.manage_notifications")
@require_POST
def notification_retry(request, pk):
    job = get_object_or_404(OutboundEmail.objects.select_related("recipient_user", "student"), pk=pk)
    ok, reason = worker.retry(job, request.user)
    if ok:
        AuditEvent.objects.create(
            actor=request.user,
            action=AuditEvent.Action.NOTIFICATION_RETRIED,
            student=job.student,
            details={"email_id": job.pk},
        )
        messages.success(request, "Queued again. The worker will try within a few seconds.")
    else:
        messages.error(request, f"Not retried: {reason}.")
    return redirect("notification-detail", pk=pk)


@perm("app1.manage_notifications")
@require_POST
def notification_cancel(request, pk):
    job = get_object_or_404(OutboundEmail, pk=pk)
    if worker.cancel(job):
        AuditEvent.objects.create(
            actor=request.user,
            action=AuditEvent.Action.NOTIFICATION_CANCELLED,
            student=job.student,
            details={"email_id": job.pk},
        )
        messages.success(request, "Cancelled.")
    else:
        messages.error(request, "Only queued messages can be cancelled.")
    return redirect("notification-detail", pk=pk)


SAMPLE = {
    "name": "Sample Student",
    "registration_number": "SAMPLE-001",
}


def _sample_context(kind):
    now = timezone.now()

    class Sample:
        name, registration_number, pk = SAMPLE["name"], SAMPLE["registration_number"], 0

    return {
        "institution": InstitutionSettings.load(),
        "student": Sample(),
        "kind": kind,
        "event_label": {"check_in": "Check-in", "check_out": "Check-out"}.get(kind, "Attendance record corrected"),
        "moment": format_moment(now),
        "recorded": format_moment(now),
        "manual": kind == "check_out",
        "past_entry": False,
        "record_url": f"{settings.SITE_URL}/students/0/" if settings.SITE_URL else "",
        "after": {"check_in_time": format_moment(now), "check_out_time": None} if kind == "correction" else None,
    }


@perm("app1.manage_notifications")
@require_GET
def template_preview(request, kind, fmt):
    if kind not in ("check_in", "check_out", "correction") or fmt not in ("html", "txt"):
        raise Http404
    body = render_to_string(f"emails/attendance_event.{fmt}", _sample_context(kind))
    if fmt == "txt":
        response = HttpResponse(body, content_type="text/plain; charset=utf-8")
        response["Cache-Control"] = "private, no-store"
        return response
    return _isolated_html(body)


@perm("app1.manage_settings")
@require_POST
def test_email(request):
    """Send one test message to the requesting admin, synchronously, rate limited."""
    if not request.user.email:
        messages.error(request, "Add an e-mail address to your own account first.")
        return redirect("settings")
    problems = email_transport_problems()
    if problems:
        messages.error(request, "E-mail is not configured: " + "; ".join(problems) + ".")
        return redirect("settings")
    if not allow_rate(f"test-email:{request.user.pk}", settings.TEST_EMAIL_INTERVAL_SECONDS):
        messages.error(request, f"Wait {settings.TEST_EMAIL_INTERVAL_SECONDS} seconds between test e-mails.")
        return redirect("settings")
    message = EmailMultiAlternatives(
        subject=f"{settings.EMAIL_SUBJECT_PREFIX}Test e-mail",
        body=render_to_string("emails/test.txt", {"institution": InstitutionSettings.load(), "user": request.user}),
        from_email=settings.DEFAULT_FROM_EMAIL or None,
        to=[request.user.email],
    )
    try:
        message.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 - shown to the admin, without secrets
        messages.error(request, f"The mail server did not accept the test message: {worker.describe(exc)}")
    else:
        AuditEvent.objects.create(actor=request.user, action=AuditEvent.Action.TEST_EMAIL_SENT)
        messages.success(
            request,
            f"Test message accepted by the mail server for {request.user.email}. "
            "Check that it arrives (and is not in spam).",
        )
    return redirect("settings")


@require_http_methods(["GET", "POST"])
def verify_email(request, token):
    """Public page reached from a verification e-mail. Confirmation needs a POST."""
    try:
        data = verification.read(token)
    except verification.VerificationError as exc:
        return render(request, "notifications/verify.html", {"error": str(exc)}, status=400)
    if request.method == "POST":
        try:
            verification.confirm(token)
        except verification.VerificationError as exc:
            return render(request, "notifications/verify.html", {"error": str(exc)}, status=400)
        return render(request, "notifications/verify.html", {"done": True, "address": data["e"]})
    return render(
        request, "notifications/verify.html", {"address": data["e"], "institution": InstitutionSettings.load()}
    )
