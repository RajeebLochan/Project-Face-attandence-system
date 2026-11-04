"""Attendance e-mail notifications.

Flow: an attendance change writes an AuditEvent and, in the same database transaction,
OutboundEmail rows (one per recipient and kind). A separate worker process
(``manage.py run_notification_worker``) sends committed rows, retries temporary
failures with backoff, and marks permanent failures. Nothing here talks to a mail
server inside a web request, except the rate-limited admin test e-mail.
"""
