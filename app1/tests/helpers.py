import datetime as dt
import shutil
import tempfile
from io import BytesIO
from pathlib import Path

import numpy as np
from django.contrib.auth.models import Group, Permission, User
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from app1.models import Batch, BatchMembership, FaceEmbedding, Student, student_photo_path
from app1.recognition.engine import reset_engine
from app1.recognition.gallery import gallery
from app1.roles import ADMIN_GROUP, INSTRUCTOR_GROUP
from app1.storage import PrivateMediaStorage

from .fakes import FakeFaceEngine, colour_embedding

RED = (32, 32, 224)  # BGR
BLUE = (224, 32, 32)
GREEN = (32, 224, 32)
GREY = (96, 96, 96)
PASSWORD = "synthetic-test-password-1"


def image_bytes(colour_bgr=RED, size=(320, 240), fmt="JPEG"):
    width, height = size
    array = np.zeros((height, width, 3), dtype=np.uint8)
    array[:, :] = colour_bgr
    buffer = BytesIO()
    Image.fromarray(array[:, :, ::-1]).save(buffer, fmt)
    return buffer.getvalue()


def upload(data, name="photo.jpg", content_type="image/jpeg"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def solid(colour_bgr, size=(320, 240)):
    width, height = size
    array = np.zeros((height, width, 3), dtype=np.uint8)
    array[:, :] = colour_bgr
    return array


def make_user(username, group=None, superuser=False, perms=()):
    if superuser:
        return User.objects.create_superuser(username, f"{username}@example.test", PASSWORD)
    user = User.objects.create_user(username, f"{username}@example.test", PASSWORD)
    if group:
        user.groups.add(Group.objects.get(name=group))
    for dotted in perms:
        app_label, codename = dotted.split(".")
        user.user_permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))
    return user


_counter = {"n": 0}


LONG_AGO = dt.date(2020, 1, 1)


def default_batch():
    batch, _ = Batch.objects.get_or_create(code="TEST-A", defaults={"name": "Test batch A"})
    return batch


def make_student(name="Test Student", *, colour=RED, authorized=True, consent=True, photo=True,
                 registration_number=None, embedding=True, model_version=FakeFaceEngine.model_version,
                 batch="default", enrolled_on=LONG_AGO):
    _counter["n"] += 1
    student = Student(
        name=name,
        email=f"student{_counter['n']}@example.test",
        registration_number=registration_number or f"REG{_counter['n']:05d}",
        branch="CSE",
        year="1st Year",
        authorized=authorized,
        consent_status=Student.Consent.GIVEN if consent else Student.Consent.NOT_RECORDED,
        enrolled_on=enrolled_on,
    )
    if photo:
        student.image.name = PrivateMediaStorage().save(
            student_photo_path(None, "x.jpg"), ContentFile(image_bytes(colour))
        )
    student.save()
    if batch == "default":
        batch = default_batch()
    if batch is not None:
        BatchMembership.objects.create(student=student, batch=batch, start_date=enrolled_on)
    if embedding:
        FaceEmbedding.objects.create(
            student=student, model_version=model_version,
            vector=colour_embedding(solid(colour)).astype("<f4").tobytes(),
        )
    return student


class AppTestCase(TestCase):
    """Common isolation: fake engine reset, empty cache, fresh gallery."""

    def setUp(self):
        super().setUp()
        media_root = Path(tempfile.mkdtemp(prefix="private-media-"))
        override = override_settings(PRIVATE_MEDIA_ROOT=media_root, MEDIA_ROOT=media_root)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, media_root, ignore_errors=True)
        self.media_root = media_root
        FakeFaceEngine.reset()
        reset_engine()
        gallery.invalidate()
        cache.clear()

    def login(self, user):
        self.client.force_login(user)
        return user

    def admin_user(self, name="admin1"):
        return make_user(name, group=ADMIN_GROUP)

    def instructor(self, name="teacher1", batches=None):
        user = make_user(name, group=INSTRUCTOR_GROUP)
        for batch in batches if batches is not None else [default_batch()]:
            batch.instructors.add(user)
        return user
