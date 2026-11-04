from django.core.management.base import BaseCommand

from app1.roles import ROLE_PERMISSIONS, sync_roles


class Command(BaseCommand):
    help = "Create or reset the Attendance admin and Instructor groups and their permissions."

    def handle(self, *args, **options):
        sync_roles()
        for name, permissions in ROLE_PERMISSIONS.items():
            self.stdout.write(f"{name}: {len(permissions)} permissions")
