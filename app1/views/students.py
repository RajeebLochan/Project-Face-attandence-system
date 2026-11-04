from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import SuspiciousFileOperation
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from ..forms import (
    AuthorizationForm,
    PhotoReplaceForm,
    ReasonForm,
    StudentDetailsForm,
    StudentEnrollmentForm,
    StudentFilterForm,
)
from ..models import AuditEvent, Student
from ..recognition.engine import ModelUnavailable
from ..recognition.service import MESSAGES, check_single_face
from ..services import students as student_service
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


def _face_from_form(form):
    """Run the single-face check on a validated photo form; add errors to the form."""
    try:
        check = check_single_face(form.image_bgr)
    except ModelUnavailable:
        form.add_error(None, MODEL_MISSING)
        return None
    if check.status != "ok":
        form.add_error("photo", MESSAGES[check.status])
        return None
    return check.face


@perm("app1.view_student")
@require_GET
def student_list(request):
    form = StudentFilterForm(request.GET or None)
    students = Student.objects.all()
    if form.is_valid():
        data = form.cleaned_data
        if data["q"]:
            students = students.filter(
                Q(name__icontains=data["q"]) | Q(registration_number__istartswith=data["q"])
            )
        if data["branch"]:
            students = students.filter(branch=data["branch"])
        if data["year"]:
            students = students.filter(year=data["year"])
        status = data["status"]
        if status == "pending":
            students = students.filter(authorized=False, consent_status=Student.Consent.GIVEN)
        elif status == "authorized":
            students = students.filter(authorized=True)
        elif status == "no_consent":
            students = students.exclude(consent_status=Student.Consent.GIVEN)
    page = Paginator(students, 25).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "students/list.html",
        {"form": form, "page": page, "query": query.urlencode()},
    )


@perm("app1.view_student")
@require_GET
def student_detail(request, pk):
    student = get_object_or_404(Student, pk=pk)
    context = {
        "student": student,
        "attendance": student.attendance_set.order_by("-date")[:10],
    }
    if request.user.has_perm("app1.view_auditevent"):
        context["events"] = AuditEvent.objects.filter(student=student).select_related("actor")[:10]
    return render(request, "students/detail.html", context)


@perm("app1.view_student_photo")
@require_GET
def student_photo(request, pk):
    student = get_object_or_404(Student, pk=pk)
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
    student = get_object_or_404(Student, pk=pk)
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
@require_http_methods(["GET", "POST"])
def student_photo_replace(request, pk):
    student = get_object_or_404(Student, pk=pk)
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
    student = get_object_or_404(Student, pk=pk)
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
    student = get_object_or_404(Student, pk=pk)
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        student_service.remove_biometrics(student, actor=request.user, reason=form.cleaned_data["reason"])
        messages.success(request, "Photo and face template deleted; consent recorded as withdrawn.")
        return redirect("student-detail", pk=student.pk)
    return render(request, "students/remove_biometrics.html", {"form": form, "student": student})


@perm("app1.delete_student")
@require_http_methods(["GET", "POST"])
def student_delete(request, pk):
    student = get_object_or_404(Student, pk=pk)
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
        {"student": student, "attendance_count": student.attendance_set.count()},
    )
