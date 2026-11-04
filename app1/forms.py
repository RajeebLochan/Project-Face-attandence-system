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
    reason = forms.CharField(min_length=5, max_length=500, widget=forms.Textarea(attrs={"rows": 3}),
                             help_text="Why is this recorded manually? Stored in the audit log.")

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
    reason = forms.CharField(min_length=5, max_length=500, widget=forms.Textarea(attrs={"rows": 3}),
                             help_text="Required. Stored in the audit log with the old and new times.")

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
