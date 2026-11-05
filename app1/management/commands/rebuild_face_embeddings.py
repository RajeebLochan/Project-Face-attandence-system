"""Regenerate face templates from stored enrolment photos for the current model version."""
from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.core.management.base import BaseCommand, CommandError

from app1.models import FaceEmbedding, Student
from app1.recognition.engine import ModelUnavailable, get_engine
from app1.recognition.service import check_single_face
from app1.services.images import ImageValidationError, decode_image
from app1.services.students import store_embedding
from app1.storage import PrivateMediaStorage


class Command(BaseCommand):
    help = (
        "Recompute face templates for consenting students after a model or preprocessing "
        "change, and delete templates that are stale or no longer allowed."
    )

    def handle(self, *args, **options):
        try:
            engine = get_engine()
        except ModelUnavailable as exc:
            raise CommandError(str(exc)) from None
        storage = PrivateMediaStorage()

        withdrawn = FaceEmbedding.objects.exclude(student__consent_status=Student.Consent.GIVEN).delete()[0]
        stale = FaceEmbedding.objects.exclude(model_version=engine.model_version).delete()[0]

        rebuilt, failed = 0, []
        students = Student.objects.filter(consent_status=Student.Consent.GIVEN).exclude(image="")
        for student in students.iterator():
            try:
                with storage.open(student.image.name, "rb") as handle:
                    image = decode_image(handle, max_bytes=settings.ENROLMENT_PHOTO_MAX_BYTES)
            except (FileNotFoundError, SuspiciousFileOperation, ImageValidationError):
                failed.append((student.pk, "photo missing or unreadable"))
                continue
            check = check_single_face(image)
            if check.status != "ok":
                failed.append((student.pk, check.status))
                FaceEmbedding.objects.filter(student=student).delete()
                continue
            store_embedding(student, check.face)
            rebuilt += 1

        self.stdout.write(f"Model version: {engine.model_version}")
        self.stdout.write(f"Templates rebuilt: {rebuilt}")
        self.stdout.write(f"Stale-version templates removed: {stale}")
        self.stdout.write(f"Templates removed for students without consent: {withdrawn}")
        if failed:
            self.stdout.write(self.style.WARNING(
                f"{len(failed)} student(s) have no usable template and will not be recognised "
                "until their photo is replaced:"
            ))
            for pk, reason in failed:
                self.stdout.write(f"  student #{pk}: {reason}")
