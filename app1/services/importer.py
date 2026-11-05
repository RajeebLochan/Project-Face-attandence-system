"""CSV student import: parse, validate and preview first, then create in one transaction.

Imported students have no photo and no consent record; face enrolment is a separate,
consented step. Duplicate registration numbers (in the file or already on record) are
reported and never merged."""

import csv
import io
from dataclasses import dataclass, field
from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import AuditEvent, Batch, BatchMembership, Student

COLUMNS = [
    "name",
    "email",
    "registration_number",
    "branch",
    "year",
    "batch_code",
    "enrolled_on",
    "guardian_name",
    "guardian_email",
]
REQUIRED = ["name", "email", "registration_number", "branch", "year"]
MAX_ROWS = 1000
MAX_BYTES = 1024 * 1024


class ImportFileError(ValueError):
    pass


@dataclass
class Row:
    line: int
    values: dict
    errors: list = field(default_factory=list)
    status: str = "new"  # new, duplicate_in_file, exists, invalid
    cleaned: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.status == "new"


def read_csv(upload):
    if upload.size > MAX_BYTES:
        raise ImportFileError("The file is larger than 1 MB.")
    raw = upload.read(MAX_BYTES + 1)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportFileError("Save the file as UTF-8 CSV and try again.") from None
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ImportFileError("The file is empty.")
    headers = [(h or "").strip().lower() for h in reader.fieldnames]
    missing = [c for c in REQUIRED if c not in headers]
    if missing:
        raise ImportFileError("Missing columns: " + ", ".join(missing) + ".")
    rows = []
    for index, raw_row in enumerate(reader, start=2):
        if index - 1 > MAX_ROWS:
            raise ImportFileError(f"The file has more than {MAX_ROWS} rows. Split it and import in parts.")
        values = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items() if k}
        if not any(values.values()):
            continue
        rows.append(Row(line=index, values={c: values.get(c, "") for c in COLUMNS}))
    if not rows:
        raise ImportFileError("The file has a header but no student rows.")
    return rows


def validate(rows):
    from ..forms import StudentDetailsForm

    batches = {b.code.lower(): b for b in Batch.objects.filter(is_active=True)}
    seen = {}
    for row in rows:
        form = StudentDetailsForm(data={k: row.values[k] for k in REQUIRED})
        form.is_valid()
        errors = {k: [str(e) for e in v] for k, v in form.errors.items()}
        number = (row.values["registration_number"] or "").strip().upper()
        exists = "registration_number" in errors and any("already exists" in e for e in errors["registration_number"])
        if exists:
            errors.pop("registration_number")
        cleaned = dict(form.cleaned_data)
        code = row.values["batch_code"].lower()
        if code:
            if code in batches:
                cleaned["batch_id"] = batches[code].pk
            else:
                errors.setdefault("batch_code", []).append("No active batch with this code.")
        enrolled = row.values["enrolled_on"]
        try:
            cleaned["enrolled_on"] = (
                date.fromisoformat(enrolled).isoformat() if enrolled else timezone.localdate().isoformat()
            )
        except ValueError:
            errors.setdefault("enrolled_on", []).append("Use the date format YYYY-MM-DD.")
        guardian_email = row.values["guardian_email"]
        if guardian_email:
            from django.core.exceptions import ValidationError
            from django.core.validators import validate_email

            try:
                validate_email(guardian_email)
                cleaned["guardian_email"] = guardian_email.lower()
            except ValidationError:
                errors.setdefault("guardian_email", []).append("Enter a valid e-mail address.")
        cleaned["guardian_name"] = row.values["guardian_name"][:255]
        row.errors = [f"{k.replace('_', ' ')}: {' '.join(v)}" for k, v in errors.items()]
        if number and number in seen:
            row.status = "duplicate_in_file"
            row.errors.insert(0, f"Same registration number as line {seen[number]}.")
        elif exists:
            row.status = "exists"
        elif row.errors:
            row.status = "invalid"
        else:
            row.cleaned = cleaned
        if number:
            seen.setdefault(number, row.line)
    return rows


def create(cleaned_rows, actor):
    """Create students from validated rows. Rows that became duplicates since the preview
    are skipped and reported."""
    created, skipped = 0, []
    with transaction.atomic():
        for data in cleaned_rows:
            enrolled_on = date.fromisoformat(data["enrolled_on"])
            try:
                with transaction.atomic():
                    student = Student.objects.create(
                        name=data["name"],
                        email=data["email"],
                        registration_number=data["registration_number"],
                        branch=data["branch"],
                        year=data["year"],
                        enrolled_on=enrolled_on,
                        guardian_name=data.get("guardian_name", ""),
                        guardian_email=data.get("guardian_email", ""),
                    )
                    if data.get("batch_id"):
                        BatchMembership.objects.create(
                            student=student, batch_id=data["batch_id"], start_date=enrolled_on
                        )
                created += 1
            except IntegrityError:
                skipped.append(data["registration_number"])
        if created:
            AuditEvent.objects.create(
                actor=actor,
                action=AuditEvent.Action.STUDENTS_IMPORTED,
                details={"created": created, "skipped": len(skipped)},
            )
    return created, skipped
