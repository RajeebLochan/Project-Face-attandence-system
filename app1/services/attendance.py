"""Transactional, idempotent attendance recording with an audit trail."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from ..models import Attendance, AuditEvent

CHECK_IN = "check_in"
CHECK_OUT = "check_out"
MODES = {CHECK_IN: "Check in", CHECK_OUT: "Check out"}


class AttendanceError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Outcome:
    status: str  # "recorded" or "already_recorded"
    attendance: Attendance


def record_event(student, mode, *, actor, source, at=None, reason="", details=None):
    """Record a check-in or check-out for ``student``.

    * The attendance date is the local date (TIME_ZONE) of the event time.
    * Repeating an event that is already recorded changes nothing ("already_recorded").
    * Check-out requires an earlier check-in on the same local date.
    """
    if mode not in MODES:
        raise AttendanceError("invalid_mode", "Unknown attendance mode.")
    at = at or timezone.now()
    if timezone.is_naive(at):
        raise AttendanceError("naive_time", "Attendance times must be timezone-aware.")
    if at > timezone.now() + timedelta(minutes=1):
        raise AttendanceError("future_time", "Attendance cannot be recorded in the future.")
    day = timezone.localdate(at)

    with transaction.atomic():
        if mode == CHECK_IN:
            attendance, created = Attendance.objects.select_for_update().get_or_create(
                student=student, date=day, defaults={"check_in_time": at}
            )
            if not created:
                if attendance.check_in_time is not None:
                    return Outcome("already_recorded", attendance)
                attendance.check_in_time = at
                attendance.save(update_fields=["check_in_time", "updated_at"])
            action = AuditEvent.Action.CHECK_IN
        else:
            attendance = (
                Attendance.objects.select_for_update().filter(student=student, date=day).first()
            )
            if attendance is None or attendance.check_in_time is None:
                raise AttendanceError("not_checked_in", "This student has not checked in today.")
            if attendance.check_out_time is not None:
                return Outcome("already_recorded", attendance)
            if at < attendance.check_in_time:
                raise AttendanceError("before_check_in", "Check-out cannot be earlier than check-in.")
            attendance.check_out_time = at
            attendance.save(update_fields=["check_out_time", "updated_at"])
            action = AuditEvent.Action.CHECK_OUT

        AuditEvent.objects.create(
            actor=actor,
            action=action,
            source=source,
            student=student,
            attendance=attendance,
            reason=reason,
            details=details or {},
        )
    return Outcome("recorded", attendance)


def _iso(value):
    return timezone.localtime(value).isoformat() if isinstance(value, datetime) else None


def correct_attendance(attendance, *, check_in, check_out, actor, reason):
    """Staff correction of an existing record. A reason is mandatory and audited."""
    if not reason or not reason.strip():
        raise AttendanceError("reason_required", "A reason is required for every correction.")
    now = timezone.now()
    for value in (check_in, check_out):
        if value is not None:
            if timezone.localdate(value) != attendance.date:
                raise AttendanceError("wrong_date", "Times must fall on the record's date.")
            if value > now:
                raise AttendanceError("future_time", "Times cannot be in the future.")
    if check_out is not None and check_in is None:
        raise AttendanceError("not_checked_in", "A check-out requires a check-in.")
    if check_in is not None and check_out is not None and check_out < check_in:
        raise AttendanceError("before_check_in", "Check-out cannot be earlier than check-in.")

    with transaction.atomic():
        locked = Attendance.objects.select_for_update().get(pk=attendance.pk)
        before = {"check_in_time": _iso(locked.check_in_time), "check_out_time": _iso(locked.check_out_time)}
        locked.check_in_time = check_in
        locked.check_out_time = check_out
        locked.save(update_fields=["check_in_time", "check_out_time", "updated_at"])
        after = {"check_in_time": _iso(check_in), "check_out_time": _iso(check_out)}
        AuditEvent.objects.create(
            actor=actor,
            action=AuditEvent.Action.ATTENDANCE_CORRECTED,
            source=AuditEvent.Source.MANUAL,
            student_id=locked.student_id,
            attendance=locked,
            reason=reason.strip(),
            details={"before": before, "after": after},
        )
    return locked
