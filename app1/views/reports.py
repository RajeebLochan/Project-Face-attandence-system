from datetime import timedelta

from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .. import scope
from ..forms import ReportFilterForm
from ..models import Batch, BatchMembership
from ..services.register import LABELS, RegisterContext, day_range, summary
from .attendance import csv_response, local_time
from .students import perm

SUMMARY_COLUMNS = ["present", "late", "checked_in", "absent", "on_leave", "holiday", "no_class", "not_recorded"]


def _batches_for(user):
    if scope.sees_all(user):
        return Batch.objects.all()
    return Batch.objects.filter(pk__in=scope.assigned_batch_ids(user))


def _report_students(user, form):
    data = form.cleaned_data
    start, end, batch = data["date_from"], data["date_to"], data.get("batch")
    students = scope.students_for(user, on=end) | scope.students_for(user, on=start)
    students = students.filter(enrolled_on__lte=end).exclude(left_on__lte=start)
    if batch is not None:
        in_batch = BatchMembership.objects.filter(batch=batch, start_date__lte=end).exclude(end_date__lte=start)
        students = students.filter(pk__in=in_batch.values("student_id"))
    return students.distinct().order_by("name", "registration_number")


def _form(request):
    today = timezone.localdate()
    data = request.GET.copy()
    data.setdefault("date_from", (today - timedelta(days=6)).isoformat())
    data.setdefault("date_to", today.isoformat())
    return ReportFilterForm(data, batches=_batches_for(request.user))


@perm("app1.view_attendance")
@require_GET
def report(request):
    form = _form(request)
    context = {"form": form, "labels": LABELS, "columns": SUMMARY_COLUMNS}
    if form.is_valid():
        students = list(_report_students(request.user, form))
        rows, totals = summary(students, form.cleaned_data["date_from"], form.cleaned_data["date_to"])
        context.update(
            rows=rows,
            totals=totals,
            student_count=len(students),
            days=(form.cleaned_data["date_to"] - form.cleaned_data["date_from"]).days + 1,
        )
    query = request.GET.copy()
    context["query"] = query.urlencode()
    return render(request, "reports/summary.html", context)


@perm("app1.view_attendance")
@require_GET
def report_export(request):
    form = _form(request)
    if not form.is_valid():
        return report(request)
    start, end = form.cleaned_data["date_from"], form.cleaned_data["date_to"]
    students = list(_report_students(request.user, form))
    context = RegisterContext(students, start, end)
    rows = []
    for student in context.students:
        for day in day_range(start, end):
            status = context.status(student, day)
            batch = context.batch_on(student.pk, day)
            record = status.record
            rows.append(
                (
                    day.isoformat(),
                    student.registration_number,
                    student.name,
                    batch.code if batch else "",
                    status.label,
                    local_time(record.check_in_time) if record else "",
                    local_time(record.check_out_time) if record else "",
                    "yes" if status.checkout_missing else "",
                )
            )
    return csv_response(
        f"attendance-report-{start.isoformat()}-to-{end.isoformat()}.csv",
        ["date", "registration_number", "name", "batch", "status", "check_in", "check_out", "checkout_missing"],
        rows,
    )


@perm("app1.view_attendance")
@require_GET
def register_day(request):
    """Printable register for one day: every student on the roster with status and times."""
    today = timezone.localdate()
    data = request.GET.copy()
    day_value = data.get("day") or today.isoformat()
    data["date_from"] = data["date_to"] = day_value
    form = ReportFilterForm(data, batches=_batches_for(request.user))
    context = {"form": form, "day": None}
    if form.is_valid():
        day = form.cleaned_data["date_from"]
        students = list(_report_students(request.user, form))
        register = RegisterContext(students, day, day)
        entries = [(s, register.status(s, day), register.batch_on(s.pk, day)) for s in register.students]
        counts = {}
        for _, status, _ in entries:
            counts[status.status] = counts.get(status.status, 0) + 1
        context.update(day=day, entries=entries, counts=[(LABELS[k], counts[k], k) for k in LABELS if k in counts])
    return render(request, "reports/register.html", context)
