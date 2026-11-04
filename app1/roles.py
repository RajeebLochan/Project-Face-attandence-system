"""Explicit role definitions. Groups are synchronised after every ``migrate``.

Assign people to these groups in the Django admin (Users -> Groups). Superusers have
every permission. Do not edit these groups' permissions in the admin: they are reset
to the lists below on the next migration (``manage.py sync_roles`` does the same).
"""

ADMIN_GROUP = "Attendance admin"
INSTRUCTOR_GROUP = "Instructor"

ROLE_PERMISSIONS = {
    ADMIN_GROUP: [
        "app1.view_student",
        "app1.add_student",
        "app1.change_student",
        "app1.delete_student",
        "app1.authorize_student",
        "app1.view_student_photo",
        "app1.view_attendance",
        "app1.add_attendance",
        "app1.change_attendance",
        "app1.use_kiosk",
        "app1.view_auditevent",
        "app1.import_students",
        "app1.view_all_batches",
        "app1.manage_batches",
        "app1.view_batch",
        "app1.view_leave",
        "app1.add_leave",
        "app1.change_leave",
        "app1.manage_settings",
        "app1.manage_staff",
        "app1.manage_notifications",
    ],
    INSTRUCTOR_GROUP: [
        "app1.view_student",
        # Needed to compare the live person with the enrolment photo when confirming.
        "app1.view_student_photo",
        "app1.view_attendance",
        "app1.add_attendance",
        "app1.change_attendance",
        "app1.use_kiosk",
        # Instructors only see students in the batches they are assigned to.
        "app1.view_batch",
        "app1.view_leave",
    ],
}


def sync_roles(apps=None, **kwargs):
    if apps is None:
        from django.apps import apps as global_apps

        apps = global_apps
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    for group_name, codenames in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        permissions = []
        for dotted in codenames:
            app_label, codename = dotted.split(".")
            permission = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
            if permission is None:
                raise RuntimeError(f"Permission {dotted} does not exist; run migrations first.")
            permissions.append(permission)
        group.permissions.set(permissions)
