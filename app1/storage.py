"""Private file storage for enrolment photos.

Files live under ``settings.PRIVATE_MEDIA_ROOT`` and have no public URL. Views read
them through :class:`PrivateMediaStorage` after checking permissions.
"""
import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateMediaStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(
            base_url=None,
            file_permissions_mode=0o600,
            directory_permissions_mode=0o700,
        )

    # Resolve the location on every access so it follows settings overrides (tests).
    @property
    def base_location(self):
        return str(settings.PRIVATE_MEDIA_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise ValueError("Private files have no public URL; use the permission-checked view.")


def private_storage():
    """Callable used by ``Student.image`` (keeps migrations independent of settings)."""
    return PrivateMediaStorage()


def safe_exists(name):
    """``exists`` that treats names escaping the private root (legacy data) as missing."""
    from django.core.exceptions import SuspiciousFileOperation

    try:
        return bool(name) and PrivateMediaStorage().exists(name)
    except SuspiciousFileOperation:
        return False
