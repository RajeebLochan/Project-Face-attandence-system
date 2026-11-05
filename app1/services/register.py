"""Daily attendance status for a student, derived from records and scheduling rules.

Statuses (shown in reports, exports and on the dashboard):

* ``present``      checked in no later than the expected start plus the grace period
* ``late``         checked in after the expected start plus the grace period
* ``checked_in``   checked in, but no schedule applies, so lateness is not judged
* ``on_leave``     approved leave covers the day and there is no check-in
* ``holiday``      institution or batch holiday and no check-in
* ``no_class``     not a working day in the batch schedule and no check-in
* ``absent``       a past working day with a schedule, on the roster, no check-in,
                   no leave and no holiday
* ``not_recorded`` nothing recorded yet: today, a future day, or a day with no
                   schedule configured (absence is never inferred without a schedule)
* ``not_enrolled`` before the enrolment date, on or after the leaving date, or (with
                   batches in use) not on any batch roster that day

A record with a check-in but no check-out on an earlier day is flagged
``checkout_missing``; the system never invents a check-out time.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from django.utils import timezone

from ..models import Attendance, BatchMembership, BatchSchedule, Holiday, Leave

LABELS = {
    "present": "Present",
    "late": "Late",
    "checked_in": "Checked in",
    "on_leave": "On leave",
    "holiday": "Holiday",
    "no_class": "No class",
    "absent": "Absent",
    "not_recorded": "Not yet recorded",
    "not_enrolled": "Not enrolled",
}
ATTENDED = {"present", "late", "checked_in"}
MAX_RANGE_DAYS = 62


@dataclass
class DayStatus:
    student_id: int
    day: date
    status: str
    record: Attendance | None = None
    checkout_missing: bool = False

    @property
    def label(self):
        return LABELS[self.status]


class RegisterContext:
    """Loads everything needed to judge a set of students over a date range in a fixed
    number of queries."""

    def __init__(self, students, start, end):
        self.students = list(students)
        self.start, self.end = start, end
        ids = [s.pk for s in self.students]
        self.records = {
            (r.student_id, r.date): r
            for r in Attendance.objects.filter(student_id__in=ids, date__gte=start, date__lte=end)
        }
        self.memberships = defaultdict(list)
        for m in BatchMembership.objects.filter(student_id__in=ids, start_date__lte=end).select_related("batch"):
            if m.end_date is None or m.end_date > start:
                self.memberships[m.student_id].append(m)
        batch_ids = {m.batch_id for ms in self.memberships.values() for m in ms}
        self.schedules = defaultdict(list)
        for s in BatchSchedule.objects.filter(batch_id__in=batch_ids, effective_from__lte=end).order_by(
            "effective_from"
        ):
            self.schedules[s.batch_id].append(s)
        self.holidays = defaultdict(set)  # date -> {None or batch_id}
        for h in Holiday.objects.filter(date__gte=start, date__lte=end):
            self.holidays[h.date].add(h.batch_id)
        self.leaves = defaultdict(list)
        for leave in Leave.objects.filter(
            student_id__in=ids, cancelled_at__isnull=True, start_date__lte=end, end_date__gte=start
        ):
            self.leaves[leave.student_id].append(leave)
        self.any_batches = BatchMembership.objects.exists()
        self.today = timezone.localdate()

    def batch_on(self, student_id, day):
        for m in self.memberships.get(student_id, ()):
            if m.covers(day):
                return m.batch
        return None

    def schedule_on(self, batch_id, day):
        current = None
        for schedule in self.schedules.get(batch_id, ()):
            if schedule.effective_from <= day:
                current = schedule
        return current

    def status(self, student, day):
        record = self.records.get((student.pk, day))
        checked_in = record is not None and record.check_in_time is not None
        missing = checked_in and record.check_out_time is None and day < self.today
        batch = self.batch_on(student.pk, day)
        schedule = self.schedule_on(batch.pk, day) if batch else None

        def result(code):
            return DayStatus(student.pk, day, code, record, missing)

        if checked_in:
            if schedule and day.weekday() in schedule.weekday_set and not self._holiday(day, batch):
                late = record.check_in_time > schedule.late_after(day)
                return result("late" if late else "present")
            return result("checked_in")
        if not student.is_active_on(day) or (self.any_batches and batch is None and self.memberships.get(student.pk)):
            return result("not_enrolled")
        if any(lv.start_date <= day <= lv.end_date for lv in self.leaves.get(student.pk, ())):
            return result("on_leave")
        if schedule is None:
            return result("not_recorded")
        if self._holiday(day, batch):
            return result("holiday")
        if day.weekday() not in schedule.weekday_set:
            return result("no_class")
        if day < self.today:
            return result("absent")
        return result("not_recorded")

    def _holiday(self, day, batch):
        marks = self.holidays.get(day, set())
        return None in marks or (batch is not None and batch.pk in marks)


def day_range(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def statuses_for_day(students, day):
    context = RegisterContext(students, day, day)
    return [context.status(student, day) for student in context.students]


def summary(students, start, end):
    """Per-student counts by status over a date range, plus totals."""
    context = RegisterContext(students, start, end)
    rows, totals = [], defaultdict(int)
    for student in context.students:
        counts = defaultdict(int)
        for day in day_range(start, end):
            status = context.status(student, day)
            counts[status.status] += 1
            if status.checkout_missing:
                counts["checkout_missing"] += 1
        for key, value in counts.items():
            totals[key] += value
        rows.append((student, dict(counts)))
    return rows, dict(totals)
