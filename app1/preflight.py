"""Detect data that would block the integrity constraints added by migration 0015.

Only primary keys and counts are reported, never names, e-mail addresses or
registration numbers. The queries touch columns that exist both before and after the
upgrade, so this works on a database still at migration 0013.
"""
from collections import defaultdict


def find_conflicts(Student, Attendance):
    problems = []

    by_number = defaultdict(list)
    for pk, number in Student.objects.values_list("pk", "registration_number").order_by("pk"):
        by_number[number].append(pk)
    for number, pks in by_number.items():
        if len(pks) > 1:
            label = "an empty" if not (number or "").strip() else "the same"
            problems.append(f"Students {pks} share {label} registration number.")

    by_day = defaultdict(list)
    for pk, student_id, day in Attendance.objects.values_list("pk", "student_id", "date").order_by("pk"):
        by_day[(student_id, day)].append(pk)
    for (student_id, day), pks in by_day.items():
        if len(pks) > 1:
            problems.append(f"Attendance rows {pks} duplicate student #{student_id} on {day}.")

    orphan_checkouts = list(
        Attendance.objects.filter(check_in_time__isnull=True, check_out_time__isnull=False)
        .values_list("pk", flat=True)
    )
    if orphan_checkouts:
        problems.append(f"Attendance rows {orphan_checkouts} have a check-out without a check-in.")
    return problems


def find_warnings(Student, branch_values, year_values, file_exists):
    warnings = []
    folded = defaultdict(list)
    for pk, number in Student.objects.values_list("pk", "registration_number").order_by("pk"):
        folded[(number or "").strip().upper()].append(pk)
    for number, pks in folded.items():
        if number == "":
            warnings.append(f"Students {pks} have an empty registration number.")
        elif len(pks) > 1:
            warnings.append(
                f"Students {pks} have registration numbers that differ only in case or spacing."
            )
    bad_branch, bad_year, missing = [], [], []
    for pk, branch, year, image in Student.objects.values_list("pk", "branch", "year", "image"):
        if branch not in branch_values:
            bad_branch.append(pk)
        if year not in year_values:
            bad_year.append(pk)
        if image and not file_exists(image):
            missing.append(pk)
    if bad_branch:
        warnings.append(f"Students {bad_branch} have a branch outside the supported list.")
    if bad_year:
        warnings.append(f"Students {bad_year} have a year outside the supported list.")
    if missing:
        warnings.append(
            f"Students {missing} reference photos missing from PRIVATE_MEDIA_ROOT "
            "(run `manage.py relocate_legacy_photos` for photos still under media/)."
        )
    return warnings
