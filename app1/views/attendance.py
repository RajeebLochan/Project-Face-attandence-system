import csv

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .. import scope
from ..forms import (
    AttendanceCorrectionForm,
    AttendanceFilterForm,
    AuditFilterForm,
    LiveAttendanceForm,
    ManualAttendanceForm,
    ReasonForm,
)
from ..models import AuditEvent
from ..services.attendance import (
    CHECK_IN,
    MODES,
    AttendanceError,
    correct_attendance,
    record_event,
    review_missing_checkout,
)
from ..services.register import statuses_for_day
from .students import perm


def _filtered_attendance(request):
    form = AttendanceFilterForm(request.GET or None)
    records = scope.attendance_for(request.user).select_related("student")
    if form.is_bound and not form.is_valid():
        return form, records.none()
    if form.is_bound:
        data = form.cleaned_data
        if data["q"]:
            records = records.filter(
                Q(student__name__icontains=data["q"]) | Q(student__registration_number__istartswith=data["q"])
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
        request,
        "attendance/list.html",
        {
            "form": form,
            "page": page,
            "query": query.urlencode(),
            "today": timezone.localdate(),
        },
    )


def csv_safe(value):
    """Neutralise spreadsheet formula injection in exported cells."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


_csv_safe = csv_safe  # kept for existing imports


def local_time(value):
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M:%S") if value else ""


def csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow([csv_safe(v) for v in row])
    return response


@perm("app1.view_attendance")
@require_GET
def attendance_export(request):
    form, records = _filtered_attendance(request)
    if form.is_bound and not form.is_valid():
        messages.error(request, "Fix the filter before exporting.")
        return redirect("student_attendance_list")
    rows = (
        (
            r.date.isoformat(),
            r.student.registration_number,
            r.student.name,
            r.student.branch,
            r.student.year,
            local_time(r.check_in_time),
            local_time(r.check_out_time),
            r.calculate_duration(),
        )
        for r in records.iterator(chunk_size=500)
    )
    return csv_response(
        "attendance.csv",
        ["date", "registration_number", "name", "branch", "year", "check_in", "check_out", "duration"],
        rows,
    )


@perm("app1.add_attendance")
@require_GET
def attendance_manual(request):
    """Search a student, see today's status, then check in or out with a reason."""
    q = request.GET.get("q", "").strip()[:100]
    students = scope.students_for(request.user)
    results = []
    if q:
        matches = list(
            students.filter(Q(name__icontains=q) | Q(registration_number__istartswith=q)).order_by("name")[:20]
        )
        results = list(zip(matches, statuses_for_day(matches, timezone.localdate()), strict=True))
    selected = None
    pk = request.GET.get("student", "")
    if pk.isdigit():
        student = students.filter(pk=int(pk)).first()
        if student is not None:
            selected = statuses_for_day([student], timezone.localdate())[0]
            selected.student = student
    form = LiveAttendanceForm(initial={"student": selected.student.pk if selected else None})
    return render(
        request,
        "attendance/manual.html",
        {
            "q": q,
            "results": results,
            "selected": selected,
            "form": form,
            "modes": MODES,
            "today": timezone.localdate(),
        },
    )


@perm("app1.add_attendance")
@require_POST
def attendance_manual_record(request):
    form = LiveAttendanceForm(request.POST)
    back = reverse("attendance-manual")
    if not form.is_valid():
        student_id = request.POST.get("student", "")
        messages.error(request, "Give a reason of at least five characters for a manual entry.")
        return redirect(f"{back}?student={student_id}" if student_id.isdigit() else back)
    student = get_object_or_404(scope.students_for(request.user), pk=form.cleaned_data["student"])
    mode = form.cleaned_data["mode"]
    try:
        outcome = record_event(
            student, mode, actor=request.user, source=AuditEvent.Source.MANUAL, reason=form.cleaned_data["reason"]
        )
    except AttendanceError as exc:
        messages.error(request, exc.message)
    else:
        label = "Check-in" if mode == CHECK_IN else "Check-out"
        if outcome.status == "already_recorded":
            messages.warning(request, f"{label} was already recorded for {student.name} today. Nothing changed.")
        else:
            moment = outcome.attendance.check_in_time if mode == CHECK_IN else outcome.attendance.check_out_time
            messages.success(request, f"{label} recorded for {student.name} at {timezone.localtime(moment):%H:%M}.")
    return redirect(f"{back}?student={student.pk}")


@perm("app1.add_attendance")
@require_http_methods(["GET", "POST"])
def attendance_manual_past(request):
    """Backdated entries are kept apart from live check-in: they need an explicit date and
    time, are flagged in the audit log, and never send "just checked in" e-mails."""
    now = timezone.localtime()
    initial = {"date": now.date(), "time": now.time().replace(second=0, microsecond=0)}
    if request.GET.get("student", "").isdigit():
        initial["student"] = int(request.GET["student"])
    form = ManualAttendanceForm(request.POST or None, initial=initial)
    form.fields["student"].queryset = scope.students_for(request.user).order_by("name", "registration_number")
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            outcome = record_event(
                data["student"],
                data["mode"],
                actor=request.user,
                source=AuditEvent.Source.MANUAL,
                at=data["at"],
                reason=data["reason"],
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
                messages.success(request, "Past entry recorded.")
            return redirect("student_attendance_list")
    return render(request, "attendance/manual_past.html", {"form": form})


@perm("app1.change_attendance")
@require_http_methods(["GET", "POST"])
def attendance_correct(request, pk):
    attendance = get_object_or_404(scope.attendance_for(request.user).select_related("student"), pk=pk)
    form = AttendanceCorrectionForm(request.POST or None, attendance=attendance)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            correct_attendance(
                attendance,
                check_in=data["check_in_at"],
                check_out=data["check_out_at"],
                actor=request.user,
                reason=data["reason"],
            )
        except AttendanceError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "Attendance corrected and logged.")
            return redirect("student_attendance_list")
    history = attendance.audit_events.select_related("actor")[:20]
    return render(
        request,
        "attendance/correct.html",
        {
            "form": form,
            "attendance": attendance,
            "history": history,
            "review_form": ReasonForm(),
        },
    )


@perm("app1.change_attendance")
@require_POST
def attendance_review_checkout(request, pk):
    attendance = get_object_or_404(scope.attendance_for(request.user), pk=pk)
    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Give a reason of at least five characters.")
        return redirect("attendance-correct", pk=pk)
    try:
        review_missing_checkout(attendance, actor=request.user, reason=form.cleaned_data["reason"])
    except AttendanceError as exc:
        messages.error(request, exc.message)
        return redirect("attendance-correct", pk=pk)
    messages.success(request, "Marked as reviewed. The record still has no check-out time.")
    return redirect("home")


@perm("app1.view_auditevent")
@require_GET
def audit_log(request):
    form = AuditFilterForm(request.GET or None)
    events = scope.audit_events_for(request.user).select_related("actor", "student", "attendance")
    if form.is_valid():
        data = form.cleaned_data
        if data["q"]:
            events = events.filter(
                Q(student__name__icontains=data["q"])
                | Q(student__registration_number__istartswith=data["q"])
                | Q(actor__username__icontains=data["q"])
            )
        if data["action"]:
            events = events.filter(action=data["action"])
        if data["date_from"]:
            events = events.filter(created_at__date__gte=data["date_from"])
        if data["date_to"]:
            events = events.filter(created_at__date__lte=data["date_to"])
    page = Paginator(events, 50).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "attendance/audit_log.html", {"form": form, "page": page, "query": query.urlencode()})
