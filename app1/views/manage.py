"""Settings, staff, batches/schedules/holidays, leave and CSV import."""

import uuid
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .. import formatting as fmt
from .. import scope
from ..forms import (
    BatchForm,
    HolidayForm,
    ImportUploadForm,
    InstitutionSettingsForm,
    LeaveForm,
    ScheduleForm,
    StaffForm,
    StaffPreferenceForm,
    role_of,
)
from ..models import (
    AuditEvent,
    Batch,
    BatchMembership,
    BatchSchedule,
    Holiday,
    InstitutionSettings,
    Leave,
    StaffNotificationPreference,
)
from ..notifications import verification
from ..notifications.core import enablement_problems, transport_summary
from ..services import importer
from .students import perm

User = get_user_model()


# --- Settings ---------------------------------------------------------------------------


@perm("app1.manage_settings")
@require_http_methods(["GET", "POST"])
def settings_page(request):
    config = InstitutionSettings.load()
    form = InstitutionSettingsForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        if form.has_changed():
            with transaction.atomic():
                saved = form.save(commit=False)
                saved.updated_by = request.user
                saved.save()
                AuditEvent.objects.create(
                    actor=request.user,
                    action=AuditEvent.Action.SETTINGS_CHANGED,
                    details={"fields": sorted(form.changed_data)},
                )
            messages.success(request, "Settings saved.")
        return redirect("settings")
    return render(
        request,
        "manage/settings.html",
        {
            "form": form,
            "config": config,
            "transport": transport_summary(),
            "problems": enablement_problems(config),
            "recipients": StaffNotificationPreference.objects.select_related("user")
            .filter(user__is_active=True)
            .filter(
                Q(receive_check_in=True)
                | Q(receive_check_out=True)
                | Q(receive_corrections=True)
                | Q(receive_digest=True)
            ),
        },
    )


# --- Staff ------------------------------------------------------------------------------


@perm("app1.manage_staff")
@require_GET
def staff_list(request):
    users = User.objects.prefetch_related("groups", "assigned_batches").select_related("notification_preference")
    users = users.order_by("-is_active", "username")
    rows = [(user, role_of(user)) for user in users]
    return render(request, "manage/staff_list.html", {"rows": rows})


def _guard_superuser(request, user):
    if user.is_superuser and not request.user.is_superuser:
        raise Http404


@perm("app1.manage_staff")
@require_http_methods(["GET", "POST"])
def staff_edit(request, pk=None):
    user = get_object_or_404(User, pk=pk) if pk else None
    if user is not None:
        _guard_superuser(request, user)
    form = StaffForm(request.POST or None, instance=user, editor=request.user)
    pref = StaffNotificationPreference.objects.get_or_create(user=user)[0] if user else None
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            saved = form.save()
            AuditEvent.objects.create(
                actor=request.user,
                action=AuditEvent.Action.STAFF_CHANGED,
                details={
                    "user_id": saved.pk,
                    "created": user is None,
                    "role": form.cleaned_data["role"],
                    "active": saved.is_active,
                },
            )
        messages.success(request, "Staff account created." if user is None else "Staff account updated.")
        return redirect("staff-edit", pk=saved.pk)
    return render(
        request,
        "manage/staff_edit.html",
        {
            "form": form,
            "staff": user,
            "pref": pref,
            "pref_form": StaffPreferenceForm(instance=pref) if pref else None,
        },
    )


@perm("app1.manage_staff")
@require_POST
def staff_preferences(request, pk):
    user = get_object_or_404(User, pk=pk)
    _guard_superuser(request, user)
    pref, _ = StaffNotificationPreference.objects.get_or_create(user=user)
    form = StaffPreferenceForm(request.POST, instance=pref)
    if form.is_valid():
        with transaction.atomic():
            form.save()
            AuditEvent.objects.create(
                actor=request.user,
                action=AuditEvent.Action.STAFF_CHANGED,
                details={"user_id": user.pk, "notifications": sorted(form.changed_data)},
            )
        messages.success(request, "Notification preferences saved.")
    return redirect("staff-edit", pk=pk)


@perm("app1.manage_staff")
@require_POST
def staff_verify_request(request, pk):
    user = get_object_or_404(User, pk=pk, is_active=True)
    try:
        created = verification.request("staff", user, request.user)
    except verification.VerificationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "Verification e-mail queued." if created else "A verification e-mail was queued in the last 10 minutes.",
        )
    return redirect("staff-edit", pk=pk)


# --- Batches, schedules, holidays -------------------------------------------------------


@perm("app1.view_batch")
@require_GET
def batch_list(request):
    today = timezone.localdate()
    batches = (
        Batch.objects.all()
        if scope.sees_all(request.user)
        else Batch.objects.filter(pk__in=scope.assigned_batch_ids(request.user))
    )
    batches = batches.annotate(
        members=Count(
            "memberships",
            filter=Q(memberships__start_date__lte=today)
            & (Q(memberships__end_date__isnull=True) | Q(memberships__end_date__gt=today)),
        )
    ).prefetch_related("instructors")
    rows = [(batch, batch.schedule_on(today)) for batch in batches]
    return render(
        request,
        "manage/batch_list.html",
        {
            "rows": rows,
            "form": BatchForm() if request.user.has_perm("app1.manage_batches") else None,
            "holidays": Holiday.objects.filter(date__gte=today).select_related("batch")[:10],
            "holiday_form": HolidayForm(initial={"date": today}),
        },
    )


def _visible_batch(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    if not scope.sees_all(request.user) and batch.pk not in scope.assigned_batch_ids(request.user):
        raise Http404
    return batch


@perm("app1.manage_batches")
@require_http_methods(["GET", "POST"])
def batch_edit(request, pk=None):
    batch = get_object_or_404(Batch, pk=pk) if pk else None
    form = BatchForm(request.POST or None, instance=batch)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            saved = form.save()
            AuditEvent.objects.create(
                actor=request.user,
                action=AuditEvent.Action.BATCH_CHANGED,
                details={"batch_id": saved.pk, "fields": sorted(form.changed_data)},
            )
        messages.success(request, "Batch saved.")
        return redirect("batch-detail", pk=saved.pk)
    return render(request, "manage/batch_edit.html", {"form": form, "batch": batch})


@perm("app1.view_batch")
@require_GET
def batch_detail(request, pk):
    batch = _visible_batch(request, pk)
    today = timezone.localdate()
    members = (
        BatchMembership.objects.filter(batch=batch, start_date__lte=today)
        .filter(Q(end_date__isnull=True) | Q(end_date__gt=today))
        .select_related("student")
        .order_by("student__name")
    )
    return render(
        request,
        "manage/batch_detail.html",
        {
            "batch": batch,
            "members": members,
            "schedules": batch.schedules.select_related("created_by"),
            "current": batch.schedule_on(today),
            "schedule_form": ScheduleForm(initial={"effective_from": today}),
            "holidays": Holiday.objects.filter(Q(batch=batch) | Q(batch__isnull=True), date__gte=today)[:20],
            "upcoming": BatchMembership.objects.filter(batch=batch, start_date__gt=today).select_related("student"),
        },
    )


@perm("app1.manage_batches")
@require_POST
def schedule_add(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    form = ScheduleForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
        return redirect("batch-detail", pk=pk)
    data = form.cleaned_data
    with transaction.atomic():
        BatchSchedule.objects.update_or_create(
            batch=batch,
            effective_from=data["effective_from"],
            defaults={
                "weekdays": ",".join(sorted(data["weekdays"])),
                "start_time": data["start_time"],
                "grace_minutes": data["grace_minutes"],
                "created_by": request.user,
            },
        )
        AuditEvent.objects.create(
            actor=request.user,
            action=AuditEvent.Action.SCHEDULE_CHANGED,
            details={"batch_id": batch.pk, "effective_from": data["effective_from"].isoformat()},
        )
    messages.success(
        request,
        f"Schedule saved; it applies from {fmt.day(data['effective_from'])}. "
        "Earlier days keep the schedule that applied then.",
    )
    return redirect("batch-detail", pk=pk)


@perm("app1.manage_batches")
@require_POST
def holiday_add(request):
    form = HolidayForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            holiday = form.save()
            AuditEvent.objects.create(
                actor=request.user,
                action=AuditEvent.Action.HOLIDAY_CHANGED,
                details={"holiday_id": holiday.pk, "date": holiday.date.isoformat(), "added": True},
            )
        messages.success(request, f"Holiday added for {fmt.day(holiday.date)}.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect(request.POST.get("next") if request.POST.get("next", "").startswith("/batches/") else "batch-list")


@perm("app1.manage_batches")
@require_POST
def holiday_delete(request, pk):
    holiday = get_object_or_404(Holiday, pk=pk)
    with transaction.atomic():
        AuditEvent.objects.create(
            actor=request.user,
            action=AuditEvent.Action.HOLIDAY_CHANGED,
            reason=request.POST.get("reason", "")[:200],
            details={"date": holiday.date.isoformat(), "removed": True},
        )
        holiday.delete()
    messages.success(request, "Holiday removed.")
    return redirect("batch-list")


# --- Leave ------------------------------------------------------------------------------


@perm("app1.view_leave")
@require_GET
def leave_list(request):
    students = scope.students_for(request.user)
    leaves = Leave.objects.filter(student__in=students).select_related("student", "recorded_by")
    if request.GET.get("show") != "all":
        leaves = leaves.filter(cancelled_at__isnull=True, end_date__gte=timezone.localdate() - timedelta(days=30))
    page = Paginator(leaves, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "manage/leave_list.html",
        {
            "page": page,
            "form": LeaveForm(students=students) if request.user.has_perm("app1.add_leave") else None,
            "show_all": request.GET.get("show") == "all",
        },
    )


@perm("app1.add_leave")
@require_POST
def leave_add(request):
    form = LeaveForm(request.POST, students=scope.students_for(request.user))
    if form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            leave = Leave.objects.create(
                student=data["student"],
                start_date=data["start_date"],
                end_date=data["end_date"],
                reason=data["reason"],
                recorded_by=request.user,
            )
            AuditEvent.objects.create(
                actor=request.user,
                action=AuditEvent.Action.LEAVE_RECORDED,
                student=data["student"],
                reason=data["reason"],
                details={
                    "leave_id": leave.pk,
                    "from": data["start_date"].isoformat(),
                    "to": data["end_date"].isoformat(),
                },
            )
        messages.success(request, f"Leave recorded for {data['student'].name}.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("leave-list")


@perm("app1.change_leave")
@require_POST
def leave_cancel(request, pk):
    leave = get_object_or_404(Leave.objects.filter(student__in=scope.students_for(request.user)), pk=pk)
    with transaction.atomic():
        updated = Leave.objects.filter(pk=leave.pk, cancelled_at__isnull=True).update(
            cancelled_at=timezone.now(), cancelled_by=request.user
        )
        if updated:
            AuditEvent.objects.create(
                actor=request.user,
                action=AuditEvent.Action.LEAVE_CANCELLED,
                student=leave.student,
                details={"leave_id": leave.pk},
            )
    messages.success(request, "Leave cancelled.")
    return redirect("leave-list")


# --- CSV import -------------------------------------------------------------------------

SESSION_KEY = "student-import"


@perm("app1.import_students")
@require_http_methods(["GET", "POST"])
def import_students(request):
    form = ImportUploadForm(request.POST or None, request.FILES or None)
    rows = None
    if request.method == "POST" and form.is_valid():
        try:
            rows = importer.validate(importer.read_csv(form.cleaned_data["file"]))
        except importer.ImportFileError as exc:
            form.add_error("file", str(exc))
        else:
            token = uuid.uuid4().hex
            request.session[SESSION_KEY] = {"token": token, "rows": [r.cleaned for r in rows if r.ok]}
            counts = {
                status: sum(1 for r in rows if r.status == status)
                for status in ("new", "exists", "duplicate_in_file", "invalid")
            }
            return render(request, "manage/import.html", {"form": form, "rows": rows, "counts": counts, "token": token})
    return render(request, "manage/import.html", {"form": form, "rows": rows})


@perm("app1.import_students")
@require_POST
def import_confirm(request):
    stored = request.session.get(SESSION_KEY)
    if not stored or stored.get("token") != request.POST.get("token"):
        messages.error(request, "The preview expired. Upload the file again.")
        return redirect("student-import")
    created, skipped = importer.create(stored["rows"], request.user)
    del request.session[SESSION_KEY]
    note = f" {len(skipped)} row(s) were skipped because the registration number now exists." if skipped else ""
    messages.success(request, f"Imported {created} student(s).{note} Photos and consent are recorded separately.")
    return redirect("student-list")
