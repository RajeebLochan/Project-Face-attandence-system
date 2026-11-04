from django.contrib.auth.views import LogoutView
from django.urls import path

from .views import attendance, auth, dashboard, kiosk, students

urlpatterns = [
    path("", dashboard.home, name="home"),
    path("privacy/", dashboard.privacy_notice, name="privacy-notice"),
    path("login/", auth.ThrottledLoginView.as_view(), name="login"),
    # LogoutView only accepts POST (CSRF-protected).
    path("logout/", LogoutView.as_view(), name="logout"),
    # Students
    path("students/", students.student_list, name="student-list"),
    path("students/enroll/", students.student_enroll, name="student-enroll"),
    path("students/<int:pk>/", students.student_detail, name="student-detail"),
    path("students/<int:pk>/edit/", students.student_edit, name="student-edit"),
    path("students/<int:pk>/photo/", students.student_photo, name="student-photo"),
    path("students/<int:pk>/photo/replace/", students.student_photo_replace, name="student-photo-replace"),
    path("students/<int:pk>/authorize/", students.student_authorize, name="student-authorize"),
    path("students/<int:pk>/remove-biometrics/", students.student_remove_biometrics,
         name="student-remove-biometrics"),
    path("students/<int:pk>/delete/", students.student_delete, name="student-delete"),
    # Attendance
    path("students/attendance/", attendance.attendance_list, name="student_attendance_list"),
    path("attendance/export.csv", attendance.attendance_export, name="attendance-export"),
    path("attendance/manual/", attendance.attendance_manual, name="attendance-manual"),
    path("attendance/<int:pk>/correct/", attendance.attendance_correct, name="attendance-correct"),
    path("audit/", attendance.audit_log, name="audit-log"),
    # Browser kiosk
    path("kiosk/", kiosk.kiosk, name="kiosk"),
    path("kiosk/recognize/", kiosk.kiosk_recognize, name="kiosk-recognize"),
    path("kiosk/confirm/", kiosk.kiosk_confirm, name="kiosk-confirm"),
]
