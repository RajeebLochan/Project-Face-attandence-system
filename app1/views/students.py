from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import SuspiciousFileOperation
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .. import formatting as fmt
from .. import scope
from ..forms import (
    AuthorizationForm,
    PhotoReplaceForm,
    ReasonForm,
    StudentBatchForm,
    StudentContactForm,
    StudentDetailsForm,
    StudentEnrollmentForm,
    StudentFilterForm,
    StudentStatusForm,
)
from ..models import Attendance, AuditEvent, BatchMembership, OutboundEmail
from ..notifications import verification
from ..recognition.engine import ModelUnavailable
from ..recognition.service import MESSAGES, check_single_face
from ..services import students as student_service
from ..services.register import statuses_for_day
from ..storage import PrivateMediaStorage

MODEL_MISSING = (
    "Face recognition models are not installed, so the photo cannot be checked. "
    "An administrator must run `python manage.py setup_face_models`."
)


def perm(codename):
    """login_required + permission check that answers 403 (not a login loop) when missing."""

    def decorator(view):
        return login_required(permission_required(codename, raise_exception=True)(view))

    return decorator


def scoped_student(request, pk):
    """The student, if this staff member may see them; otherwise 404 (no existence leak)."""
    return get_object_or_404(scope.students_for(request.user), pk=pk)


def _face_from_form(form):
    try:
        check = check_single_face(form.image_bgr)
    except ModelUnavailable:
        form.add_error(None, MODEL_MISSING)
        return None
    if check.status != "ok":
        form.add_error("photo", MESSAGES[check.status])
        return None
    return check.face


def current_membership(student):
    today = timezone.localdate()
    return (
        BatchMembership.objects.filter(student=student, start_date__lte=today)
        .filter(Q(end_date__isnull=True) | Q(end_date__gt=today))
        .select_related("batch")
        .first()
    )


@perm("app1.view_student")
@require_GET
def student_list(request):
    form = StudentFilterForm(request.GET or None)
    students = scope.students_for(request.user)
    today = timezone.localdate()
    show = request.GET.get("show", "active")
    if show == "active":
        students = students.filter(enrolled_on__lte=today).filter(Q(left_on__isnull=True) | Q(left_on__gt=today))
    elif show == "left":
        students = students.filter(left_on__lte=today)
    if form.is_valid():
        data = form.cleaned_data
        if data["q"]:
            students = students.filter(Q(name__icontains=data["q"]) | Q(registration_number__istartswith=data["q"]))
        if data["branch"]:
            students = students.filter(branch=data["branch"])
        if data["year"]:
            students = students.filter(year=data["year"])
        status = data["status"]
        if status == "pending":
            students = students.filter(authorized=False, consent_status="given").exclude(image="")
        elif status == "authorized":
            students = students.filter(authorized=True)
        elif status == "no_consent":
            students = students.exclude(consent_status="given")
    current = BatchMembership.objects.filter(student=OuterRef("pk"), start_date__lte=today).filter(
        Q(end_date__isnull=True) | Q(end_date__gt=today)
    )
    students = students.annotate(has_batch=Exists(current))
    page = Paginator(students, 25).get_page(request.GET.get("page"))
    batches = {
        m.student_id: m.batch
        for m in BatchMembership.objects.filter(student__in=page.object_list, start_date__lte=today)
        .filter(Q(end_date__isnull=True) | Q(end_date__gt=today))
        .select_related("batch")
    }
    rows = [(student, batches.get(student.pk)) for student in page.object_list]
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "students/list.html",
        {
            "form": form,
            "page": page,
            "rows": rows,
            "query": query.urlencode(),
            "show": show,
        },
    )


@perm("app1.view_student")
@require_GET
def student_detail(request, pk):
    student = scoped_student(request, pk)
    today = timezone.localdate()
    context = {
        "student": student,
        "today_status": statuses_for_day([student], today)[0],
        "attendance": student.attendance_set.order_by("-date")[:14],
        "membership": current_membership(student),
        "memberships": student.memberships.select_related("batch").order_by("-start_date"),
        "leaves": student.leaves.filter(cancelled_at__isnull=True).order_by("-start_date")[:5],
        "contact_form": StudentContactForm(instance=student),
        "batch_form": StudentBatchForm(initial={"effective": today}),
        "status_form": StudentStatusForm(initial={"effective": today}),
        "recent_emails": OutboundEmail.objects.filter(student=student).order_by("-created_at")[:5]
        if request.user.has_perm("app1.manage_notifications")
        else None,
    }
    if request.user.has_perm("app1.view_auditevent"):
        context["events"] = AuditEvent.objects.filter(student=student).select_related("actor")[:10]
    return render(request, "students/detail.html", context)


@perm("app1.view_student_photo")
@require_GET
def student_photo(request, pk):
    student = scoped_student(request, pk)
    if not student.image:
        raise Http404("No photo")
    try:
        handle = PrivateMediaStorage().open(student.image.name, "rb")
    except (FileNotFoundError, SuspiciousFileOperation):
        raise Http404("No photo") from None
    response = FileResponse(handle, content_type="image/jpeg")
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["Content-Disposition"] = 'inline; filename="enrolment-photo.jpg"'
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


@perm("app1.add_student")
@require_http_methods(["GET", "POST"])
def student_enroll(request):
    form = StudentEnrollmentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        face = _face_from_form(form)
        if face is not None:
            try:
                student = student_service.enroll_student(
                    form.save(commit=False), image_bgr=form.image_bgr, face=face, actor=request.user
                )
            except IntegrityError:
                form.add_error("registration_number", "A student with this registration number already exists.")
            else:
                messages.success(request, "Student enrolled. An administrator must review and authorize the photo.")
                return redirect("student-detail", pk=student.pk)
    return render(request, "students/enroll.html", {"form": form})


@perm("app1.change_student")
@require_http_methods(["GET", "POST"])
def student_edit(request, pk):
    student = scoped_student(request, pk)
    form = StudentDetailsForm(request.POST or None, instance=student)
    if request.method == "POST" and form.is_valid():
        if form.has_changed():
            try:
                student_service.update_details(form.save(commit=False), form.changed_data, actor=request.user)
            except IntegrityError:
                form.add_error("registration_number", "A student with this registration number already exists.")
                return render(request, "students/edit.html", {"form": form, "student": student})
            messages.success(request, "Student details updated.")
        return redirect("student-detail", pk=student.pk)
    return render(request, "students/edit.html", {"form": form, "student": student})


@perm("app1.change_student")
@require_POST
def student_contacts(request, pk):
    student = scoped_student(request, pk)
    form = StudentContactForm(request.POST, instance=student)
    if form.is_valid():
        if form.has_changed():
            with transaction.atomic():
                form.save()
                AuditEvent.objects.create(
                    actor=request.user,
                    action=AuditEvent.Action.CONTACTS_CHANGED,
                    student=student,
                    details={"fields": sorted(form.changed_data)},
                )
            messages.success(request, "Contacts and e-mail preferences saved.")
        return redirect("student-detail", pk=student.pk)
    messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("student-detail", pk=student.pk)


@perm("app1.change_student")
@require_POST
def student_verify_request(request, pk, role):
    student = scoped_student(request, pk)
    if role not in ("student", "guardian"):
        raise Http404
    try:
        created = verification.request(role, student, request.user)
    except verification.VerificationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "Verification e-mail queued."
            if created
            else "A verification e-mail was queued in the last 10 minutes; wait before sending another.",
        )
    return redirect("student-detail", pk=student.pk)


@perm("app1.change_student")
@require_POST
def student_batch(request, pk):
    student = scoped_student(request, pk)
    form = StudentBatchForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
        return redirect("student-detail", pk=student.pk)
    batch, effective = form.cleaned_data["batch"], form.cleaned_data["effective"]
    effective = max(effective, student.enrolled_on)
    with transaction.atomic():
        open_rows = (
            BatchMembership.objects.select_for_update()
            .filter(student=student)
            .filter(Q(end_date__isnull=True) | Q(end_date__gt=effective))
        )
        for membership in open_rows:
            if membership.start_date >= effective:
                if membership.start_date > timezone.localdate():
                    membership.delete()  # a future assignment that never took effect
                    continue
                messages.error(request, "This student already changed batch on that date.")
                return redirect("student-detail", pk=student.pk)
            membership.end_date = effective
            membership.save(update_fields=["end_date"])
        if batch is not None:
            BatchMembership.objects.create(student=student, batch=batch, start_date=effective)
        AuditEvent.objects.create(
            actor=request.user,
            action=AuditEvent.Action.BATCH_CHANGED,
            student=student,
            details={"batch_id": batch.pk if batch else None, "from": effective.isoformat()},
        )
    messages.success(request, f"Batch updated from {fmt.day(effective)}.")
    return redirect("student-detail", pk=student.pk)


@perm("app1.change_student")
@require_POST
def student_status(request, pk):
    student = scoped_student(request, pk)
    form = StudentStatusForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
        return redirect("student-detail", pk=student.pk)
    action = form.cleaned_data["action"]
    with transaction.atomic():
        if action == "deactivate":
            effective = form.cleaned_data["effective"] or timezone.localdate()
            student.left_on = max(effective, student.enrolled_on + timedelta(days=1))
        else:
            student.left_on = None
        student.save(update_fields=["left_on", "updated_at"])
        AuditEvent.objects.create(
            actor=request.user,
            action=AuditEvent.Action.STATUS_CHANGED,
            student=student,
            reason=form.cleaned_data["reason"],
            details={"left_on": student.left_on.isoformat() if student.left_on else None},
        )
    messages.success(request, "Student marked as left." if student.left_on else "Student reactivated.")
    return redirect("student-detail", pk=student.pk)


@perm("app1.change_student")
@require_http_methods(["GET", "POST"])
def student_photo_replace(request, pk):
    student = scoped_student(request, pk)
    form = PhotoReplaceForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        face = _face_from_form(form)
        if face is not None:
            student_service.replace_photo(student, image_bgr=form.image_bgr, face=face, actor=request.user)
            messages.success(request, "Photo replaced. The student must be authorized again.")
            return redirect("student-detail", pk=student.pk)
    return render(request, "students/photo_replace.html", {"form": form, "student": student})


@perm("app1.authorize_student")
@require_http_methods(["GET", "POST"])
def student_authorize(request, pk):
    student = scoped_student(request, pk)
    initial = {"decision": "revoke" if student.authorized else "authorize"}
    form = AuthorizationForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        authorize = form.cleaned_data["decision"] == "authorize"
        try:
            student_service.set_authorization(student, authorize, actor=request.user)
        except student_service.StudentStateError as exc:
            form.add_error(None, exc.message)
        except ModelUnavailable:
            form.add_error(None, MODEL_MISSING)
        else:
            messages.success(request, "Student authorized." if authorize else "Authorization revoked.")
            return redirect("student-detail", pk=student.pk)
    return render(request, "students/authorize.html", {"form": form, "student": student})


@perm("app1.change_student")
@require_http_methods(["GET", "POST"])
def student_remove_biometrics(request, pk):
    student = scoped_student(request, pk)
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        student_service.remove_biometrics(student, actor=request.user, reason=form.cleaned_data["reason"])
        messages.success(request, "Photo and face template deleted; consent recorded as withdrawn.")
        return redirect("student-detail", pk=student.pk)
    return render(request, "students/remove_biometrics.html", {"form": form, "student": student})


@perm("app1.delete_student")
@require_http_methods(["GET", "POST"])
def student_delete(request, pk):
    student = scoped_student(request, pk)
    if request.method == "POST":
        try:
            student_service.delete_student(student, actor=request.user)
        except student_service.StudentStateError as exc:
            messages.error(request, exc.message)
            return redirect("student-detail", pk=student.pk)
        messages.success(request, "Student deleted.")
        return redirect("student-list")
    return render(
        request,
        "students/delete.html",
        {
            "student": student,
            "attendance_count": Attendance.objects.filter(student=student).count(),
        },
    )
