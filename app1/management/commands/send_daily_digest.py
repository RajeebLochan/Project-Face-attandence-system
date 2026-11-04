"""Queue the optional daily summary e-mails. Safe to run repeatedly: one per address per day."""
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from app1.models import InstitutionSettings
from app1.notifications.digest import enqueue_digests, is_due, parse_day


class Command(BaseCommand):
    help = "Queue the daily attendance summary for staff who opted in (disabled by default)."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Summarise this date (YYYY-MM-DD) instead of today.")
        parser.add_argument("--if-due", action="store_true", help="Only run after the configured summary time.")
        parser.add_argument("--loop", type=int, default=0, metavar="SECONDS",
                            help="Keep running, checking every SECONDS (for a scheduler container).")

    def handle(self, *args, date=None, if_due=False, loop=0, **options):
        try:
            day = parse_day(date) if date else None
        except ValueError:
            raise CommandError("Use --date YYYY-MM-DD.") from None
        while True:
            close_old_connections()
            config = InstitutionSettings.load()
            if not config.digest_enabled:
                self.stdout.write("Daily summary is disabled in Settings.")
            elif if_due and not is_due(config):
                self.stdout.write("Not due yet.")
            else:
                created = enqueue_digests(day)
                self.stdout.write(f"Queued {created} summary e-mail(s).")
            if not loop:
                break
            time.sleep(loop)
