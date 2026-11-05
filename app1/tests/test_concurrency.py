"""Concurrent writers on the configured database backend (SQLite file DB or PostgreSQL).

These use real threads and real transactions. SQLite serialises writers (IMMEDIATE
transactions + busy timeout); PostgreSQL relies on the unique constraint and row locks.
Run them against PostgreSQL by setting DATABASE_URL (see README / CI).
"""
import threading

from django.db import connection
from django.test import TransactionTestCase, tag

from app1.models import Attendance, AuditEvent
from app1.services.attendance import CHECK_IN, CHECK_OUT, record_event

from .helpers import make_student, make_user


@tag("concurrency")
class ConcurrentAttendanceTests(TransactionTestCase):
    WORKERS = 8

    def run_parallel(self, func):
        barrier = threading.Barrier(self.WORKERS)
        results, errors = [], []
        lock = threading.Lock()

        def worker():
            try:
                barrier.wait(timeout=10)
                outcome = func()
                with lock:
                    results.append(outcome)
            except Exception as exc:  # noqa: BLE001 - collected and asserted below
                with lock:
                    errors.append(repr(exc))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(self.WORKERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(errors, [])
        return results

    def test_simultaneous_check_ins_create_exactly_one_record_and_event(self):
        student = make_student(embedding=False)
        staff = make_user("kiosk-staff")
        results = self.run_parallel(
            lambda: record_event(student, CHECK_IN, actor=staff, source="kiosk").status
        )
        self.assertEqual(sorted(results), ["already_recorded"] * (self.WORKERS - 1) + ["recorded"])
        self.assertEqual(Attendance.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="check_in").count(), 1)

    def test_simultaneous_check_outs_record_once(self):
        student = make_student(embedding=False)
        staff = make_user("kiosk-staff")
        record_event(student, CHECK_IN, actor=staff, source="kiosk")
        results = self.run_parallel(
            lambda: record_event(student, CHECK_OUT, actor=staff, source="kiosk").status
        )
        self.assertEqual(results.count("recorded"), 1)
        self.assertEqual(AuditEvent.objects.filter(action="check_out").count(), 1)
