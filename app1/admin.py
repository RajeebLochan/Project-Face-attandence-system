"""Read-only admin views.

Enrolment, photos, authorisation, consent and attendance changes go through the app's
own views so that they are validated and audited. Face templates are not registered.
"""
from django.contrib import admin

from .models import Attendance, AuditEvent, Student


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Student)
class StudentAdmin(ReadOnlyAdmin):
    list_display = ["id", "name", "registration_number", "branch", "year", "authorized", "consent_status"]
    list_filter = ["branch", "year", "authorized", "consent_status"]
    search_fields = ["name", "registration_number"]
    exclude = ["image"]


@admin.register(Attendance)
class AttendanceAdmin(ReadOnlyAdmin):
    list_display = ["id", "student", "date", "check_in_time", "check_out_time"]
    list_filter = ["date"]
    search_fields = ["student__name", "student__registration_number"]
    list_select_related = ["student"]


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "action", "source", "actor", "student", "attendance"]
    list_filter = ["action", "source"]
    list_select_related = ["actor", "student", "attendance"]
