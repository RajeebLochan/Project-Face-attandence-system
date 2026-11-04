from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .. import scope
from ..models import BatchSchedule, InstitutionSettings, OutboundEmail
from ..notifications import worker
from ..notifications.core import email_transport_problems
from ..recognition.model_files import MODEL_FILES
from ..services.register import LABELS, RegisterContext

ANY_APP_PERMISSION = ("app1.view_student", "app1.view_attendance", "app1.use_kiosk")
STRIP_ORDER = ["present", "late", "checked_in", "on_leave", "not_recorded", "absent", "holiday", "no_class"]


def models_installed():
    """Cheap readiness hint (size only). Checksums are verified when the engine loads."""
    return all(m.path.is_file() and m.path.stat().st_size == m.size for m in MODEL_FILES)


def _timeline(entries, day):
    """Check-in positions on a 06:00–20:00 axis for the day strip (percent of width)."""
    start_minutes, span = 6 * 60, 14 * 60
    marks = []
    for _student, status in entries:
        record = status.record
        if record and record.check_in_time:
            local = timezone.localtime(record.check_in_time)
            minutes = local.hour * 60 + local.minute
            position = min(max((minutes - start_minutes) / span * 1000, 0), 998)
            marks.append(
                {
                    "x": round(position, 1),
                    "status": status.status,
                    "time": f"{local:%H:%M}",
                    "late": status.status == "late",
                }
            )
    return marks


def _expected_marker(students, day):
    schedules = {}
    for schedule in BatchSchedule.objects.filter(effective_from__lte=day).order_by("effective_from"):
        schedules[schedule.batch_id] = schedule
    starts = sorted({(s.start_time, s.grace_minutes) for s in schedules.values()})
    if not starts:
        return None
    start_time, grace = starts[0]
    minutes = start_time.hour * 60 + start_time.minute
    return {
        "x": round((minutes - 360) / 840 * 1000, 1),
        "grace": round(grace / 840 * 1000, 1),
        "label": f"{start_time:%H:%M}",
        "grace_minutes": grace,
        "several": len(starts) > 1,
    }


def _strip(counts, total):
    segments, x = [], 0.0
    for key in STRIP_ORDER:
        if counts.get(key):
            width = counts[key] / total * 100
            segments.append(
                {"status": key, "label": LABELS[key], "count": counts[key], "x": round(x, 3), "width": round(width, 3)}
            )
            x += width
    return segments


HOURS = ["06:00", "08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00"]
RULES = [round(i * 1000 / 14, 1) for i in range(0, 15)]


@login_required
def home(request):
    user = request.user
    if not any(user.has_perm(p) for p in ANY_APP_PERMISSION):
        raise PermissionDenied
    today = timezone.localdate()
    context = {"today": today, "models_ready": models_installed(), "labels": LABELS, "hours": HOURS, "rules": RULES}
    if user.has_perm("app1.view_attendance") or user.has_perm("app1.view_student"):
        students = list(scope.students_for(user).filter(enrolled_on__lte=today).exclude(left_on__lte=today))
        register = RegisterContext(students, today, today)
        entries = [(s, register.status(s, today)) for s in register.students]
        counts = {}
        for _, status in entries:
            counts[status.status] = counts.get(status.status, 0) + 1
        total = len(entries)
        context.update(
            roster_total=total,
            strip=_strip(counts, total),
            arrived=sum(counts.get(k, 0) for k in ("present", "late", "checked_in")),
            late=counts.get("late", 0),
            not_recorded=counts.get("not_recorded", 0),
            checked_out=sum(1 for _, s in entries if s.record and s.record.check_out_time),
            timeline=_timeline(entries, today),
            expected=_expected_marker(students, today),
        )
    if user.has_perm("app1.view_attendance"):
        context["open_records"] = (
            scope.attendance_for(user)
            .filter(
                date__lt=today,
                date__gte=today - timedelta(days=14),
                check_in_time__isnull=False,
                check_out_time__isnull=True,
                checkout_review_at__isnull=True,
            )
            .select_related("student")
            .order_by("-date")[:8]
        )
        context["recent"] = (
            scope.audit_events_for(user)
            .filter(action__in=["check_in", "check_out", "attendance_corrected"])
            .select_related("student", "actor", "attendance")[:8]
        )
    if user.has_perm("app1.authorize_student"):
        context["pending"] = (
            scope.students_for(user)
            .filter(authorized=False, consent_status="given")
            .exclude(image="")
            .order_by("created_at")[:6]
        )
    issues = []
    if not context["models_ready"]:
        issues.append(
            (
                "Face models are not installed; the kiosk cannot recognise anyone. "
                "Run python manage.py setup_face_models.",
                None,
            )
        )
    if user.has_perm("app1.manage_notifications"):
        config = InstitutionSettings.load()
        failed = OutboundEmail.objects.filter(status=OutboundEmail.Status.FAILED).count()
        if failed:
            issues.append((f"{failed} notification e-mail{'s' if failed != 1 else ''} failed.", "notifications"))
        queued = OutboundEmail.objects.filter(status=OutboundEmail.Status.QUEUED).exists()
        if (config.notifications_enabled or queued) and worker.worker_is_stale():
            issues.append(
                ("The notification worker has not reported in recently; e-mails are waiting.", "notifications")
            )
        if config.notifications_enabled and email_transport_problems():
            issues.append(("E-mail notifications are on but the mail server is not configured.", "settings"))
    context["issues"] = issues
    return render(request, "home.html", context)


def privacy_notice(request):
    return render(request, "privacy_notice.html")


@never_cache
@require_GET
def healthz(request):
    """Liveness: the process answers. No database, no configuration details."""
    return HttpResponse("ok", content_type="text/plain")


@never_cache
@require_GET
def readyz(request):
    """Readiness: database reachable and all migrations applied. Reveals nothing else."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    except Exception:  # noqa: BLE001
        return JsonResponse({"status": "unavailable"}, status=503)
    if pending:
        return JsonResponse({"status": "migrations pending"}, status=503)
    return JsonResponse({"status": "ready"})
