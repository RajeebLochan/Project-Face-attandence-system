import csv

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from ..forms import AttendanceCorrectionForm, AttendanceFilterForm, ManualAttendanceForm
from ..models import Attendance, AuditEvent
from ..services.attendance import AttendanceError, correct_attendance, record_event
from .students import perm


def _filtered_attendance(request):
    form = AttendanceFilterForm(request.GET or None)
    records = Attendance.objects.select_related("student")
    if form.is_bound and not form.is_valid():
        return form, records.none()
    if form.is_bound:
        data = form.cleaned_data
        if data["q"]:
            records = records.filter(
                Q(student__name__icontains=data["q"])
                | Q(student__registration_number__istartswith=data["q"])
            )
        if data["date_from"]:
            records = records.filter(date__gte=data["date_from"])
        if data["date_to"]:
            records = records.filter(date__lte=data["date_to"])
    return form, records.order_by("-date", "student__name", "pk")


@perm("app1.view_attendance")
@require_GET
def attendance_list(request):
    form, records = _filtered_attendance(request)
    page = Paginator(records, 50).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request, "attendance/list.html", {"form": form, "page": page, "query": query.urlencode()}
    )


def _csv_safe(value):
    """Neutralise spreadsheet formula injection in exported cells."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _local(value):
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M:%S") if value else ""


@perm("app1.view_attendance")
@require_GET
def attendance_export(request):
    form, records = _filtered_attendance(request)
    if form.is_bound and not form.is_valid():
        messages.error(request, "Fix the filter before exporting.")
        return redirect("student_attendance_list")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="attendance.csv"'
    response["Cache-Control"] = "private, no-store"
    writer = csv.writer(response)
    writer.writerow(["date", "registration_number", "name", "branch", "year",
                     "check_in", "check_out", "duration"])
    for record in records.iterator(chunk_size=500):
        student = record.student
        writer.writerow([_csv_safe(v) for v in (
            record.date.isoformat(), student.registration_number, student.name, student.branch,
            student.year, _local(record.check_in_time), _local(record.check_out_time),
            record.calculate_duration(),
        )])
    return response


@perm("app1.add_attendance")
@require_http_methods(["GET", "POST"])
def attendance_manual(request):
    now = timezone.localtime()
    initial = {"date": now.date(), "time": now.time().replace(second=0, microsecond=0)}
    if request.GET.get("student", "").isdigit():
        initial["student"] = int(request.GET["student"])
    form = ManualAttendanceForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            outcome = record_event(
                data["student"], data["mode"], actor=request.user,
                source=AuditEvent.Source.MANUAL, at=data["at"], reason=data["reason"],
            )
        except AttendanceError as exc:
            form.add_error(None, exc.message)
        else:
            if outcome.status == "already_recorded":
                messages.warning(
                    request,
                    "That was already recorded for this date, so nothing changed. "
                    "Use “Correct” to change an existing time.",
                )
            else:
                messages.success(request, "Attendance recorded.")
            return redirect("student_attendance_list")
    return render(request, "attendance/manual.html", {"form": form})


@perm("app1.change_attendance")
@require_http_methods(["GET", "POST"])
def attendance_correct(request, pk):
    attendance = get_object_or_404(Attendance.objects.select_related("student"), pk=pk)
    form = AttendanceCorrectionForm(request.POST or None, attendance=attendance)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            correct_attendance(
                attendance, check_in=data["check_in_at"], check_out=data["check_out_at"],
                actor=request.user, reason=data["reason"],
            )
        except AttendanceError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "Attendance corrected and logged.")
            return redirect("student_attendance_list")
    history = attendance.audit_events.select_related("actor")[:20]
    return render(
        request, "attendance/correct.html",
        {"form": form, "attendance": attendance, "history": history},
    )


@perm("app1.view_auditevent")
@require_GET
def audit_log(request):
    events = AuditEvent.objects.select_related("actor", "student", "attendance")
    action = request.GET.get("action", "")
    if action in AuditEvent.Action.values:
        events = events.filter(action=action)
    page = Paginator(events, 50).get_page(request.GET.get("page"))
    return render(
        request, "attendance/audit_log.html",
        {"page": page, "actions": AuditEvent.Action.choices, "selected_action": action},
    )
