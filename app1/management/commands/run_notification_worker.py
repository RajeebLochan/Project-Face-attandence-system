"""Send queued notification e-mails. Run one or more of these alongside the web server."""

import os
import signal
import socket
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from app1.notifications import worker


class Command(BaseCommand):
    help = "Deliver queued attendance e-mails (database-backed queue). Use --once for cron or tests."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process due jobs once and exit.")
        parser.add_argument("--batch-size", type=int, default=20)
        parser.add_argument("--name", default="", help="Worker name (defaults to host:pid).")
        parser.add_argument(
            "--healthcheck", action="store_true", help="Exit 0 if a worker has reported in recently, 1 otherwise."
        )

    def handle(self, *args, once=False, batch_size=20, name="", healthcheck=False, **options):
        if healthcheck:
            if worker.worker_is_stale():
                raise CommandError("No notification worker has reported in recently.")
            self.stdout.write("ok")
            return
        name = name or f"{socket.gethostname()}:{os.getpid()}"
        self._stop = False
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)
        self.stdout.write(f"Notification worker {name} started")
        while not self._stop:
            close_old_connections()
            requeued, failed = worker.recover_stale()
            if requeued or failed:
                self.stdout.write(f"Recovered {requeued} stale job(s), {failed} failed")
            counts = worker.process_batch(name, batch_size)
            processed = sum(counts.values())
            worker.beat(name, processed)
            if processed:
                self.stdout.write(
                    f"{timezone.now():%Y-%m-%d %H:%M:%S} " + ", ".join(f"{k} {v}" for k, v in counts.items())
                )
            if once:
                break
            if processed < batch_size:
                time.sleep(settings.NOTIFICATION_WORKER_POLL_SECONDS)
        self.stdout.write(f"Notification worker {name} stopped")

    def _request_stop(self, *args):
        self._stop = True
