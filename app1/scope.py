"""Which students and attendance records a staff member may see or act on.

Users with ``app1.view_all_batches`` (attendance admins, superusers) see everyone.
Instructors see students who were in one of their assigned batches on the day in
question. Every view and export goes through these helpers, so direct URLs are
checked on the server, not just hidden in the interface.
"""

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import Attendance, AuditEvent, BatchMembership, OutboundEmail, Student


def sees_all(user):
    return user.has_perm("app1.view_all_batches")


def assigned_batch_ids(user):
    if not user.is_authenticated:
        return []
    return list(user.assigned_batches.filter(is_active=True).values_list("pk", flat=True))


def _membership_on(batch_ids, day_ref):
    return BatchMembership.objects.filter(student=OuterRef("pk"), batch__in=batch_ids).filter(
        Q(start_date__lte=day_ref), Q(end_date__isnull=True) | Q(end_date__gt=day_ref)
    )


def students_for(user, on=None):
    students = Student.objects.all()
    if sees_all(user):
        return students
    day = on or timezone.localdate()
    return students.filter(Exists(_membership_on(assigned_batch_ids(user), day)))


def attendance_for(user):
    records = Attendance.objects.all()
    if sees_all(user):
        return records
    membership = BatchMembership.objects.filter(
        student=OuterRef("student"), batch__in=assigned_batch_ids(user), start_date__lte=OuterRef("date")
    ).filter(Q(end_date__isnull=True) | Q(end_date__gt=OuterRef("date")))
    return records.filter(Exists(membership))


def audit_events_for(user):
    events = AuditEvent.objects.all()
    if sees_all(user):
        return events
    return events.filter(student__in=students_for(user))


def emails_for(user):
    if sees_all(user):
        return OutboundEmail.objects.all()
    return OutboundEmail.objects.none()


def can_access_student(user, student, on=None):
    if sees_all(user):
        return True
    return students_for(user, on=on).filter(pk=student.pk).exists()
