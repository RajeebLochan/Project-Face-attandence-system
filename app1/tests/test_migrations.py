"""Upgrading an existing database from the audited schema (migration 0013)."""
import datetime as dt
from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

OLD = [("app1", "0013_student_year")]
# The old schema declared year as max_length=7 although the old form posted "1st Year"
# (8 characters). SQLite ignores the limit; PostgreSQL rejects it. Use a value that the
# old column accepts on the backend under test.
LEGACY_YEAR = "1st Year" if connection.vendor == "sqlite" else "1st Yr"


class UpgradeFrom0013Tests(TransactionTestCase):
    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(OLD)
        self.old_apps = self.executor.loader.project_state(OLD).apps

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM app1_attendance")
            cursor.execute("DELETE FROM app1_student")
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def migrate_to_latest(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def old_student(self, number, **fields):
        Student = self.old_apps.get_model("app1", "Student")
        values = {"name": "Old Student", "email": "old@example.test", "registration_number": number,
                  "branch": "CSE", "year": LEGACY_YEAR, "image": "students/Old_Student.jpg", "authorized": True}
        values.update(fields)
        return Student.objects.create(**values)

    def test_clean_upgrade_preserves_data_and_disables_recognition_until_consent(self):
        Attendance = self.old_apps.get_model("app1", "Attendance")
        Camera = self.old_apps.get_model("app1", "CameraConfiguration")
        student = self.old_student("OLD-1")
        Attendance.objects.create(student=student, date=dt.date(2025, 5, 14),
                                  check_in_time=dt.datetime(2025, 5, 14, 4, 0, tzinfo=dt.UTC))
        Camera.objects.create(name="Lab", camera_source="0", threshold=0.6)

        self.migrate_to_latest()

        from app1.models import Attendance as NewAttendance
        from app1.models import Student as NewStudent

        upgraded = NewStudent.objects.get(pk=student.pk)
        self.assertEqual(upgraded.registration_number, "OLD-1")
        self.assertEqual(upgraded.year, LEGACY_YEAR)
        NewStudent.objects.filter(pk=student.pk).update(year="4th Year")  # fits the widened column
        self.assertTrue(upgraded.authorized)
        self.assertEqual(upgraded.consent_status, NewStudent.Consent.NOT_RECORDED)
        self.assertFalse(upgraded.is_recognition_eligible)
        self.assertEqual(upgraded.embeddings.count(), 0)
        self.assertEqual(NewAttendance.objects.get().date, dt.date(2025, 5, 14))
        table_names = connection.introspection.table_names()
        self.assertNotIn("app1_cameraconfiguration", table_names)

    def test_enrolled_on_backfill_keeps_past_attendance_on_the_register(self):
        Attendance = self.old_apps.get_model("app1", "Attendance")
        student = self.old_student("OLD-9")  # the old schema has no created_at
        Attendance.objects.create(student=student, date=dt.date(2025, 2, 1))
        self.migrate_to_latest()
        from app1.models import Student as NewStudent

        upgraded = NewStudent.objects.get(pk=student.pk)
        self.assertEqual(upgraded.enrolled_on, dt.date(2025, 2, 1))
        self.assertTrue(upgraded.is_active_on(dt.date(2025, 2, 1)))

    def test_duplicate_registration_numbers_block_the_upgrade_without_changing_data(self):
        first = self.old_student("SAME")
        second = self.old_student("SAME", name="Another Student")
        with self.assertRaises(RuntimeError) as ctx:
            self.migrate_to_latest()
        message = str(ctx.exception)
        self.assertIn("Upgrade blocked", message)
        self.assertIn(f"[{first.pk}, {second.pk}]", message)
        self.assertNotIn("Old Student", message)  # primary keys only, no personal data
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM app1_student")
            self.assertEqual(cursor.fetchone()[0], 2)
        # The owner resolves the conflict explicitly, then the upgrade succeeds.
        with connection.cursor() as cursor:
            cursor.execute("UPDATE app1_student SET registration_number = 'SAME-2' WHERE id = %s", [second.pk])
        self.migrate_to_latest()

    def test_duplicate_attendance_rows_block_the_upgrade(self):
        Attendance = self.old_apps.get_model("app1", "Attendance")
        student = self.old_student("OLD-2")
        rows = [Attendance.objects.create(student=student, date=dt.date(2025, 6, 1)) for _ in range(3)]
        with self.assertRaises(RuntimeError) as ctx:
            self.migrate_to_latest()
        self.assertIn(str([row.pk for row in rows]), str(ctx.exception))

    def test_preflight_command_on_old_schema(self):
        self.old_student("DUP")
        self.old_student("DUP")
        self.old_student("dup ")
        self.old_student("X-1", branch="Unknown branch")
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("attendance_preflight", stdout=out)
        output = out.getvalue()
        self.assertIn("BLOCKING: Students", output)
        self.assertIn("differ only in case", output)
        self.assertIn("branch outside the supported list", output)
        self.assertIn("missing from PRIVATE_MEDIA_ROOT", output)
        self.assertNotIn("Old Student", output)
