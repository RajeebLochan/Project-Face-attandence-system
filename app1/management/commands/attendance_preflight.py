"""Report data problems before/after upgrading. Prints primary keys only, never personal data."""
from django.core.management.base import BaseCommand, CommandError

from app1.models import BRANCH_CHOICES, YEAR_CHOICES, Attendance, Student
from app1.preflight import find_conflicts, find_warnings
from app1.storage import safe_exists


class Command(BaseCommand):
    help = "Check for data that blocks the integrity constraints, plus non-blocking warnings."

    def handle(self, *args, **options):
        conflicts = find_conflicts(Student, Attendance)
        warnings = find_warnings(
            Student,
            {value for value, _ in BRANCH_CHOICES},
            {value for value, _ in YEAR_CHOICES},
            safe_exists,
        )
        for line in warnings:
            self.stdout.write(self.style.WARNING(f"WARNING: {line}"))
        for line in conflicts:
            self.stdout.write(self.style.ERROR(f"BLOCKING: {line}"))
        if conflicts:
            raise CommandError(
                f"{len(conflicts)} blocking conflict(s). Resolve them as described in docs/UPGRADING.md."
            )
        self.stdout.write(self.style.SUCCESS("No blocking conflicts found."))
