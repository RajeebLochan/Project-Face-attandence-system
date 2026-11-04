from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone

from ..models import Attendance, Student
from ..recognition.model_files import MODEL_FILES

ANY_APP_PERMISSION = ("app1.view_student", "app1.view_attendance", "app1.use_kiosk")


def models_installed():
    """Cheap readiness hint (size only). Checksums are verified when the engine loads."""
    return all(m.path.is_file() and m.path.stat().st_size == m.size for m in MODEL_FILES)


@login_required
def home(request):
    user = request.user
    if not any(user.has_perm(p) for p in ANY_APP_PERMISSION):
        raise PermissionDenied
    today = timezone.localdate()
    context = {"today": today, "models_ready": models_installed()}
    if user.has_perm("app1.view_student"):
        students = Student.objects.all()
        context.update(
            total_students=students.count(),
            pending_authorization=students.filter(
                authorized=False, consent_status=Student.Consent.GIVEN
            ).exclude(image="").count(),
            eligible_students=students.filter(
                authorized=True, consent_status=Student.Consent.GIVEN
            ).exclude(image="").count(),
        )
    if user.has_perm("app1.view_attendance"):
        todays = Attendance.objects.filter(date=today)
        context.update(
            checked_in_today=todays.filter(check_in_time__isnull=False).count(),
            checked_out_today=todays.filter(check_out_time__isnull=False).count(),
            recent=todays.select_related("student").order_by("-updated_at")[:10],
        )
    return render(request, "home.html", context)


def privacy_notice(request):
    return render(request, "privacy_notice.html")
