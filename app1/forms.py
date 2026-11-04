import re
from datetime import datetime, time

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.utils import timezone

from .models import BRANCH_CHOICES, YEAR_CHOICES, Attendance, Student
from .services.attendance import MODES
from .services.images import ImageValidationError, decode_image

REGISTRATION_NUMBER_RE = re.compile(r"^[A-Z0-9][A-Z0-9/-]{2,14}$")


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        max_length=150, widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True})
    )
    password = forms.CharField(strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class PhotoFieldMixin:
    """Decode and validate the uploaded photo; the decoded image is kept on the form."""

    image_bgr = None

    def clean_photo(self):
        upload = self.cleaned_data.get("photo")
        try:
            self.image_bgr = decode_image(upload, max_bytes=settings.ENROLMENT_PHOTO_MAX_BYTES)
        except ImageValidationError as exc:
            raise forms.ValidationError(exc.message, code=exc.code) from None
        return upload


def consent_field():
    return forms.BooleanField(
        required=True,
        label=(
            "The student (or their parent/guardian, if under 18) has received privacy notice "
            f"version {settings.PRIVACY_NOTICE_VERSION} and given written consent to face recognition."
        ),
        error_messages={"required": "Consent must be recorded before a face photo is stored."},
    )


class StudentDetailsForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ["name", "email", "registration_number", "branch", "year"]
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "off"}),
            "email": forms.EmailInput(attrs={"autocomplete": "off"}),
            "registration_number": forms.TextInput(attrs={"autocomplete": "off"}),
        }
        help_texts = {"registration_number": "3–15 characters: letters, digits, “/” or “-”."}

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if len(name) < 2:
            raise forms.ValidationError("Enter the student's full name.")
        return name

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_registration_number(self):
        number = self.cleaned_data["registration_number"].strip().upper()
        if not REGISTRATION_NUMBER_RE.fullmatch(number):
            raise forms.ValidationError(
                "Use 3–15 characters: letters, digits, “/” or “-”, starting with a letter or digit."
            )
        clash = Student.objects.filter(registration_number__iexact=number)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A student with this registration number already exists.")
        return number


class StudentEnrollmentForm(PhotoFieldMixin, StudentDetailsForm):
    photo = forms.FileField(
        label="Face photo",
        help_text="JPEG or PNG showing exactly one face. Use the camera or choose a file.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["consent"] = consent_field()


class PhotoReplaceForm(PhotoFieldMixin, forms.Form):
    photo = forms.FileField(
        label="New face photo",
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["consent"] = consent_field()


class AuthorizationForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[("authorize", "Authorize for recognition"), ("revoke", "Revoke authorization")],
        widget=forms.RadioSelect,
    )


class ReasonForm(forms.Form):
    reason = forms.CharField(
        min_length=5,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Recorded in the audit log.",
    )


class AttendanceFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=100, label="Name or registration number")
    date_from = forms.DateField(required=False, label="From", widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, label="To", widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end and start > end:
            raise forms.ValidationError("“From” must be on or before “To”.")
        return cleaned


class StudentFilterForm(forms.Form):
    STATUS_CHOICES = [
        ("", "All"),
        ("pending", "Awaiting authorization"),
        ("authorized", "Authorized"),
        ("no_consent", "No consent / withdrawn"),
    ]
    q = forms.CharField(required=False, max_length=100, label="Name or registration number")
    branch = forms.ChoiceField(required=False, choices=[("", "All branches")] + BRANCH_CHOICES)
    year = forms.ChoiceField(required=False, choices=[("", "All years")] + YEAR_CHOICES)
    status = forms.ChoiceField(required=False, choices=STATUS_CHOICES)


def _aware(day, clock):
    return timezone.make_aware(datetime.combine(day, clock), timezone.get_current_timezone())


class ManualAttendanceForm(forms.Form):
    student = forms.ModelChoiceField(queryset=Student.objects.order_by("name", "registration_number"))
    mode = forms.ChoiceField(choices=list(MODES.items()), widget=forms.RadioSelect, initial="check_in")
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time", "step": 60}))
    reason = forms.CharField(
        min_length=5,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Why is this recorded manually? Stored in the audit log.",
    )

    def clean(self):
        cleaned = super().clean()
        day, clock = cleaned.get("date"), cleaned.get("time")
        if day and clock:
            at = _aware(day, clock)
            if at > timezone.now():
                raise forms.ValidationError("Attendance cannot be recorded in the future.")
            cleaned["at"] = at
        return cleaned


class AttendanceCorrectionForm(forms.Form):
    check_in = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time", "step": 60}))
    check_out = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time", "step": 60}))
    reason = forms.CharField(
        min_length=5,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Required. Stored in the audit log with the old and new times.",
    )

    def __init__(self, *args, attendance: Attendance, **kwargs):
        self.attendance = attendance
        local = timezone.localtime
        initial = kwargs.setdefault("initial", {})
        if attendance.check_in_time:
            initial.setdefault("check_in", local(attendance.check_in_time).time().replace(second=0, microsecond=0))
        if attendance.check_out_time:
            initial.setdefault("check_out", local(attendance.check_out_time).time().replace(second=0, microsecond=0))
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        day = self.attendance.date
        check_in, check_out = cleaned.get("check_in"), cleaned.get("check_out")
        cleaned["check_in_at"] = _aware(day, check_in) if isinstance(check_in, time) else None
        cleaned["check_out_at"] = _aware(day, check_out) if isinstance(check_out, time) else None
        if cleaned["check_out_at"] and not cleaned["check_in_at"]:
            raise forms.ValidationError("A check-out time requires a check-in time.")
        if cleaned["check_in_at"] and cleaned["check_out_at"] and cleaned["check_out_at"] < cleaned["check_in_at"]:
            raise forms.ValidationError("Check-out cannot be earlier than check-in.")
        now = timezone.now()
        if any(value and value > now for value in (cleaned["check_in_at"], cleaned["check_out_at"])):
            raise forms.ValidationError("Times cannot be in the future.")
        return cleaned


class KioskFrameForm(forms.Form):
    mode = forms.ChoiceField(choices=list(MODES.items()))
    frame = forms.FileField()


# --- Operations forms ---------------------------------------------------------------

from django.contrib.auth import get_user_model, password_validation  # noqa: E402
from django.contrib.auth.models import Group  # noqa: E402

from .models import (  # noqa: E402
    WEEKDAY_CHOICES,
    AuditEvent,
    Batch,
    Holiday,
    InstitutionSettings,
    OutboundEmail,
    StaffNotificationPreference,
)
from .roles import ADMIN_GROUP, INSTRUCTOR_GROUP  # noqa: E402
from .services.register import MAX_RANGE_DAYS  # noqa: E402

DATE_INPUT = forms.DateInput(attrs={"type": "date"})
TIME_INPUT = forms.TimeInput(attrs={"type": "time", "step": 60})


def not_before_today(value, label="This date"):
    if value and value < timezone.localdate():
        raise forms.ValidationError(f"{label} cannot be in the past; past days keep the rules that applied then.")
    return value


class StudentContactForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ["guardian_name", "guardian_email", "notify_student", "notify_guardian"]
        labels = {
            "notify_student": "E-mail the student when attendance is recorded",
            "notify_guardian": "E-mail the parent or guardian when attendance is recorded",
        }
        help_texts = {"guardian_email": "Mail is only sent after the address has been verified."}

    def clean_guardian_email(self):
        return self.cleaned_data["guardian_email"].strip().lower()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("notify_guardian") and not cleaned.get("guardian_email"):
            self.add_error("guardian_email", "Add the guardian's e-mail address first.")
        return cleaned


class StudentStatusForm(forms.Form):
    action = forms.ChoiceField(
        choices=[("deactivate", "Mark as left"), ("reactivate", "Reactivate")], widget=forms.HiddenInput
    )
    effective = forms.DateField(label="Last day on the register is the day before", widget=DATE_INPUT, required=False)
    reason = forms.CharField(min_length=5, max_length=300, widget=forms.Textarea(attrs={"rows": 2}))

    def clean_effective(self):
        return not_before_today(self.cleaned_data.get("effective"), "The leaving date")


class StudentBatchForm(forms.Form):
    batch = forms.ModelChoiceField(
        queryset=Batch.objects.filter(is_active=True), required=False, empty_label="No batch"
    )
    effective = forms.DateField(label="From", widget=DATE_INPUT)

    def clean_effective(self):
        return not_before_today(self.cleaned_data.get("effective"), "The change date")


class BatchForm(forms.ModelForm):
    class Meta:
        model = Batch
        fields = ["name", "code", "is_active", "instructors"]
        widgets = {"instructors": forms.CheckboxSelectMultiple}
        labels = {"is_active": "Active"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["instructors"].queryset = (
            get_user_model().objects.filter(is_active=True, groups__name=INSTRUCTOR_GROUP).order_by("username")
        )
        self.fields["instructors"].required = False

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,19}", code):
            raise forms.ValidationError("Use 2–20 letters, digits or hyphens.")
        return code


class ScheduleForm(forms.Form):
    effective_from = forms.DateField(widget=DATE_INPUT)
    weekdays = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        initial=["0", "1", "2", "3", "4"],
        label="Working days",
    )
    start_time = forms.TimeField(widget=TIME_INPUT, label="Expected start")
    grace_minutes = forms.IntegerField(min_value=0, max_value=180, initial=10, label="Grace period (minutes)")

    def clean_effective_from(self):
        return not_before_today(self.cleaned_data.get("effective_from"), "A new schedule")


class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ["date", "name", "batch"]
        widgets = {"date": DATE_INPUT}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["batch"].empty_label = "All batches"
        self.fields["batch"].required = False

    def clean(self):
        cleaned = super().clean()
        day, batch = cleaned.get("date"), cleaned.get("batch")
        if day and Holiday.objects.filter(date=day, batch=batch).exists():
            raise forms.ValidationError("A holiday already exists for that date and batch.")
        return cleaned


class LeaveForm(forms.Form):
    student = forms.ModelChoiceField(queryset=Student.objects.none())
    start_date = forms.DateField(widget=DATE_INPUT)
    end_date = forms.DateField(widget=DATE_INPUT)
    reason = forms.CharField(min_length=3, max_length=300)

    def __init__(self, *args, students=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = (students if students is not None else Student.objects.all()).order_by(
            "name", "registration_number"
        )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            raise forms.ValidationError("The end date must be on or after the start date.")
        if start and end and (end - start).days > 366:
            raise forms.ValidationError("Leave longer than a year should be recorded as the student leaving.")
        return cleaned


class InstitutionSettingsForm(forms.ModelForm):
    class Meta:
        model = InstitutionSettings
        fields = [
            "name",
            "contact_email",
            "contact_phone",
            "notifications_enabled",
            "notify_check_in",
            "notify_check_out",
            "notify_corrections",
            "digest_enabled",
            "digest_time",
        ]
        widgets = {"digest_time": TIME_INPUT}
        labels = {"notifications_enabled": "Send attendance e-mails"}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("notifications_enabled") or cleaned.get("digest_enabled"):
            from .notifications.core import enablement_problems

            preview = InstitutionSettings(name=cleaned.get("name", ""), contact_email=cleaned.get("contact_email", ""))
            problems = enablement_problems(preview)
            if problems:
                raise forms.ValidationError("E-mails cannot be switched on yet: " + "; ".join(problems) + ".")
        return cleaned


ROLE_CHOICES = [("instructor", "Instructor"), ("admin", "Attendance admin"), ("none", "No access")]
ROLE_GROUPS = {"admin": ADMIN_GROUP, "instructor": INSTRUCTOR_GROUP}


def role_of(user):
    names = set(user.groups.values_list("name", flat=True))
    if ADMIN_GROUP in names:
        return "admin"
    if INSTRUCTOR_GROUP in names:
        return "instructor"
    return "none"


class StaffForm(forms.Form):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False, help_text="Needed for password resets and notifications.")
    role = forms.ChoiceField(choices=ROLE_CHOICES)
    batches = forms.ModelMultipleChoiceField(
        queryset=Batch.objects.filter(is_active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Instructors only see students in these batches.",
    )
    is_active = forms.BooleanField(required=False, initial=True, label="Account active")
    password = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Only for new accounts. The person can change it with “Forgot password”.",
    )

    def __init__(self, *args, instance=None, editor=None, **kwargs):
        self.instance, self.editor = instance, editor
        if instance is not None:
            kwargs.setdefault("initial", {}).update(
                {
                    "username": instance.username,
                    "first_name": instance.first_name,
                    "last_name": instance.last_name,
                    "email": instance.email,
                    "role": role_of(instance),
                    "is_active": instance.is_active,
                    "batches": list(instance.assigned_batches.values_list("pk", flat=True)),
                }
            )
        super().__init__(*args, **kwargs)
        if instance is not None:
            del self.fields["password"]
            self.fields["username"].disabled = True

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if self.instance is None and get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("This username is taken.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        clash = get_user_model().objects.filter(email__iexact=email) if email else None
        if clash is not None and self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash is not None and clash.exists():
            raise forms.ValidationError("Another account uses this e-mail address.")
        return email

    def clean(self):
        cleaned = super().clean()
        if self.instance is not None and self.instance == self.editor:
            if cleaned.get("is_active") is False or cleaned.get("role") != role_of(self.instance):
                raise forms.ValidationError("You cannot change your own role or deactivate your own account.")
        if self.instance is None:
            password = cleaned.get("password")
            if not password:
                self.add_error("password", "Set an initial password.")
            else:
                user = get_user_model()(
                    username=cleaned.get("username", ""),
                    email=cleaned.get("email", ""),
                    first_name=cleaned.get("first_name", ""),
                    last_name=cleaned.get("last_name", ""),
                )
                try:
                    password_validation.validate_password(password, user)
                except forms.ValidationError as exc:
                    self.add_error("password", exc)
        return cleaned

    def save(self):
        User = get_user_model()
        data = self.cleaned_data
        user = self.instance or User(username=data["username"])
        for field in ("first_name", "last_name", "email", "is_active"):
            setattr(user, field, data[field])
        if self.instance is None:
            user.set_password(data["password"])
        user.save()
        groups = [Group.objects.get(name=name) for name in ROLE_GROUPS.values()]
        user.groups.remove(*groups)
        if data["role"] in ROLE_GROUPS:
            user.groups.add(Group.objects.get(name=ROLE_GROUPS[data["role"]]))
        user.assigned_batches.set(data["batches"] if data["role"] == "instructor" else [])
        return user


class StaffPreferenceForm(forms.ModelForm):
    class Meta:
        model = StaffNotificationPreference
        fields = ["receive_check_in", "receive_check_out", "receive_corrections", "receive_digest"]
        labels = {
            "receive_check_in": "Check-ins",
            "receive_check_out": "Check-outs",
            "receive_corrections": "Corrections and past entries",
            "receive_digest": "Daily summary",
        }


class ImportUploadForm(forms.Form):
    file = forms.FileField(label="CSV file", widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}))


class LiveAttendanceForm(forms.Form):
    student = forms.IntegerField(widget=forms.HiddenInput)
    mode = forms.ChoiceField(choices=list(MODES.items()), widget=forms.HiddenInput)
    reason = forms.CharField(
        min_length=5,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Why is this recorded manually? Stored in the audit log.",
    )


class AuditFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=100, label="Student, registration no. or staff")
    action = forms.ChoiceField(required=False, choices=[("", "All actions")] + list(AuditEvent.Action.choices))
    date_from = forms.DateField(required=False, label="From", widget=DATE_INPUT)
    date_to = forms.DateField(required=False, label="To", widget=DATE_INPUT)


class ReportFilterForm(forms.Form):
    date_from = forms.DateField(label="From", widget=DATE_INPUT)
    date_to = forms.DateField(label="To", widget=DATE_INPUT)
    batch = forms.ModelChoiceField(queryset=Batch.objects.none(), required=False, empty_label="All batches")

    def __init__(self, *args, batches=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["batch"].queryset = batches if batches is not None else Batch.objects.all()

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end:
            if start > end:
                raise forms.ValidationError("“From” must be on or before “To”.")
            if (end - start).days >= MAX_RANGE_DAYS:
                raise forms.ValidationError(f"Choose a range of at most {MAX_RANGE_DAYS} days.")
        return cleaned


class NotificationFilterForm(forms.Form):
    status = forms.ChoiceField(required=False, choices=[("", "All statuses")] + list(OutboundEmail.Status.choices))
    kind = forms.ChoiceField(required=False, choices=[("", "All types")] + list(OutboundEmail.Kind.choices))
