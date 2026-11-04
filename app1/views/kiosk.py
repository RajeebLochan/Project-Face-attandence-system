"""Browser kiosk: the browser captures frames, the server analyses each one.

Each request analyses exactly one bounded-size frame and returns. Nothing here opens
a server camera, starts threads, shows windows or plays sound. A recognised face only
produces a *candidate*: a staff member confirms it, and the confirmation carries a
server-signed token, so a client can never assert a student ID or a match by itself.
"""

import math
import threading
from functools import wraps

from django.conf import settings
from django.core import signing
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .. import scope
from ..forms import KioskFrameForm
from ..models import Attendance, AuditEvent, Student
from ..recognition.engine import ModelUnavailable
from ..recognition.service import MESSAGES, identify
from ..services.attendance import MODES, AttendanceError, record_event
from ..services.images import ImageValidationError, decode_image
from ..throttle import allow_rate
from .students import perm

TOKEN_SALT = "app1.kiosk.candidate"

_slots_lock = threading.Lock()
_slots = {"size": None, "semaphore": None}


def _analysis_slots():
    """Process-wide limit on simultaneous analyses (re-created if the setting changes)."""
    size = settings.KIOSK_MAX_CONCURRENT_ANALYSES
    with _slots_lock:
        if _slots["size"] != size:
            _slots["size"], _slots["semaphore"] = size, threading.BoundedSemaphore(size)
        return _slots["semaphore"]


def _json(status, http_status=200, **extra):
    response = JsonResponse({"status": status, **extra}, status=http_status)
    response["Cache-Control"] = "no-store"
    return response


def api_permission(codename):
    """JSON-friendly auth: 401/403 JSON instead of an HTML login redirect."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return _json("unauthenticated", 401, message="Your session has expired. Log in again.")
            if not request.user.has_perm(codename):
                return _json("forbidden", 403, message="You do not have permission to use the kiosk.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def _eligible_students(user=None):
    """Recognition needs authorisation + consent + photo, the student must be on the
    register today, and an instructor only sees students in their own batches."""
    today = timezone.localdate()
    students = scope.students_for(user) if user is not None else Student.objects.all()
    return (
        students.filter(authorized=True, consent_status=Student.Consent.GIVEN, enrolled_on__lte=today)
        .exclude(image="")
        .exclude(left_on__lte=today)
    )


@perm("app1.use_kiosk")
@require_GET
def kiosk(request):
    return render(
        request,
        "kiosk.html",
        {
            "modes": MODES.items(),
            "min_interval_ms": int(settings.KIOSK_MIN_FRAME_INTERVAL_SECONDS * 1000),
            "max_frame_bytes": settings.KIOSK_FRAME_MAX_BYTES,
        },
    )


@require_POST
@api_permission("app1.use_kiosk")
def kiosk_recognize(request):
    interval = settings.KIOSK_MIN_FRAME_INTERVAL_SECONDS
    if not allow_rate(f"kiosk:{request.user.pk}", interval):
        response = _json("throttled", 429, retry_after_ms=int(interval * 1000))
        response["Retry-After"] = str(max(1, math.ceil(interval)))
        return response

    form = KioskFrameForm(request.POST, request.FILES)
    if not form.is_valid():
        return _json("invalid_request", 400, message="A frame and a valid mode are required.")
    mode = form.cleaned_data["mode"]
    upload = form.cleaned_data["frame"]
    if upload.size > settings.KIOSK_FRAME_MAX_BYTES:
        return _json("frame_too_large", 413, message="The frame is too large.")
    try:
        image = decode_image(upload, max_bytes=settings.KIOSK_FRAME_MAX_BYTES)
    except ImageValidationError as exc:
        return _json("invalid_frame", 400, reason=exc.code, message=exc.message)

    slots = _analysis_slots()
    if not slots.acquire(blocking=False):
        return _json("busy", 503, retry_after_ms=500, message="The server is busy; retrying.")
    try:
        result = identify(image)
    except ModelUnavailable:
        return _json(
            "model_unavailable", 503, message="Face recognition is not available on this server. Use manual attendance."
        )
    finally:
        slots.release()

    if result.status in MESSAGES:
        return _json("unsuitable", reason=result.status, message=MESSAGES[result.status])
    if result.status == "ambiguous":
        return _json(
            "ambiguous", message="No confident match (two enrolled students are too similar). Use manual attendance."
        )
    if result.status != "match":
        return _json("unknown", message="Not recognised. If this person is enrolled, use manual attendance.")

    student = _eligible_students(request.user).filter(pk=result.match.student_id).first()
    if student is None:  # eligibility changed between caching and now
        return _json("unknown", message="Not recognised. If this person is enrolled, use manual attendance.")

    distance = round(result.match.distance, 4)
    token = signing.dumps(
        {"student": student.pk, "user": request.user.pk, "mode": mode, "distance": distance},
        salt=TOKEN_SALT,
    )
    today = Attendance.objects.filter(student=student, date=timezone.localdate()).first()
    can_view_photo = request.user.has_perm("app1.view_student_photo")
    return _json(
        "candidate",
        mode=mode,
        candidate={
            "token": token,
            "name": student.name,
            "registration_number": student.registration_number,
            "branch": student.get_branch_display(),
            "year": student.get_year_display(),
            "photo_url": reverse("student-photo", args=[student.pk]) if can_view_photo else None,
            "distance": distance,
            "threshold": settings.FACE_MATCH_THRESHOLD,
            "server_time": timezone.localtime().strftime("%H:%M"),
            "checked_in_at": _clock(today.check_in_time) if today else None,
            "checked_out_at": _clock(today.check_out_time) if today else None,
        },
    )


def _clock(value):
    return timezone.localtime(value).strftime("%H:%M") if value else None


@require_POST
@api_permission("app1.use_kiosk")
def kiosk_confirm(request):
    try:
        data = signing.loads(
            request.POST.get("token", ""),
            salt=TOKEN_SALT,
            max_age=settings.KIOSK_CANDIDATE_MAX_AGE_SECONDS,
        )
    except signing.SignatureExpired:
        return _json("expired", 400, message="This match has expired. Scan again.")
    except signing.BadSignature:
        return _json("invalid_token", 400, message="Invalid confirmation.")
    if data.get("user") != request.user.pk or data.get("mode") not in MODES:
        return _json("invalid_token", 400, message="Invalid confirmation.")

    student = _eligible_students(request.user).filter(pk=data.get("student")).first()
    if student is None:
        return _json("not_eligible", 409, message="This student is no longer authorized for recognition.")
    try:
        outcome = record_event(
            student,
            data["mode"],
            actor=request.user,
            source=AuditEvent.Source.KIOSK,
            details={"match_distance": data.get("distance"), "match_threshold": settings.FACE_MATCH_THRESHOLD},
        )
    except AttendanceError as exc:
        return _json(exc.code, 409, message=exc.message)
    attendance = outcome.attendance
    return _json(
        outcome.status,
        mode=data["mode"],
        name=student.name,
        checked_in_at=_clock(attendance.check_in_time),
        checked_out_at=_clock(attendance.check_out_time),
    )
