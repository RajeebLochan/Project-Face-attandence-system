from django.conf import settings

SECTIONS = {
    "home": "home",
    "kiosk": "kiosk",
    "student_attendance_list": "attendance",
    "attendance-manual": "attendance",
    "attendance-correct": "attendance",
    "audit-log": "audit",
}


def navigation(request):
    match = getattr(request, "resolver_match", None)
    name = getattr(match, "url_name", "") or ""
    section = "students" if name.startswith("student-") else SECTIONS.get(name, "")
    return {
        "nav_section": section,
        "privacy_notice_version": settings.PRIVACY_NOTICE_VERSION,
        "time_zone": settings.TIME_ZONE,
    }
