from django.conf import settings

from .models import InstitutionSettings, OutboundEmail

SECTIONS = {
    "home": "home",
    "kiosk": "kiosk",
    "attendance-manual": "manual",
    "attendance-manual-past": "manual",
    "student_attendance_list": "records",
    "attendance-correct": "records",
    "reports": "reports",
    "register-day": "reports",
    "audit-log": "audit",
    "notifications": "notifications",
    "notification-detail": "notifications",
    "settings": "settings",
    "staff-list": "staff",
    "staff-new": "staff",
    "staff-edit": "staff",
    "batch-list": "batches",
    "batch-new": "batches",
    "batch-detail": "batches",
    "batch-edit": "batches",
    "leave-list": "leave",
}


def navigation(request):
    match = getattr(request, "resolver_match", None)
    name = getattr(match, "url_name", "") or ""
    section = "students" if name.startswith("student-") else SECTIONS.get(name, "")
    context = {
        "nav_section": section,
        "privacy_notice_version": settings.PRIVACY_NOTICE_VERSION,
        "time_zone": settings.TIME_ZONE,
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        context["institution_name"] = InstitutionSettings.load().name
        if user.has_perm("app1.manage_notifications"):
            context["failed_emails"] = OutboundEmail.objects.filter(status=OutboundEmail.Status.FAILED).count()
    return context
