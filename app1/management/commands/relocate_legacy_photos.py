"""Move photos from the old public media/ directory into private storage."""
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from app1.models import Student, student_photo_path
from app1.services.images import ImageValidationError, decode_image, encode_jpeg
from app1.storage import PrivateMediaStorage, safe_exists


class Command(BaseCommand):
    help = (
        "For students whose photo is not in PRIVATE_MEDIA_ROOT, find it under --source (the old "
        "MEDIA_ROOT, usually ./media), re-encode it and store it privately under a random name."
    )

    def add_arguments(self, parser):
        parser.add_argument("--source", type=Path, required=True, help="Old media directory.")
        parser.add_argument("--delete-source", action="store_true",
                            help="Delete each original after it has been copied successfully.")

    def handle(self, *args, source, delete_source=False, **options):
        source = source.resolve()
        if not source.is_dir():
            raise CommandError(f"{source} is not a directory.")
        storage = PrivateMediaStorage()
        moved, problems = 0, []
        for student in Student.objects.exclude(image="").iterator():
            name = student.image.name
            if safe_exists(name):
                continue
            original = (source / name).resolve()
            if source not in original.parents or not original.is_file():
                problems.append((student.pk, "photo not found"))
                continue
            try:
                with open(original, "rb") as handle:
                    image = decode_image(handle, max_bytes=settings.ENROLMENT_PHOTO_MAX_BYTES)
            except ImageValidationError as exc:
                problems.append((student.pk, exc.code))
                continue
            new_name = storage.save(student_photo_path(student, "photo.jpg"), ContentFile(encode_jpeg(image)))
            student.image.name = new_name
            student.save(update_fields=["image", "updated_at"])
            moved += 1
            if delete_source:
                original.unlink()
        self.stdout.write(f"Photos moved to private storage: {moved}")
        for pk, reason in problems:
            self.stdout.write(self.style.WARNING(f"  student #{pk}: {reason}"))
        if moved:
            self.stdout.write("Run `python manage.py rebuild_face_embeddings` to create face templates.")
