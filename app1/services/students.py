"""Student enrolment lifecycle: photos, embeddings, consent, authorisation and deletion.

File-system side effects are tied to the database transaction: new files are removed
again if the transaction fails, and old files are deleted only after it commits.
"""

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from ..models import AuditEvent, FaceEmbedding, Student, student_photo_path
from ..recognition.engine import get_engine
from ..storage import PrivateMediaStorage
from .images import encode_jpeg


class StudentStateError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _delete_file_on_commit(name):
    if name:
        transaction.on_commit(lambda: PrivateMediaStorage().delete(name))


def _write_photo(image_bgr):
    storage = PrivateMediaStorage()
    return storage.save(student_photo_path(None, "photo.jpg"), ContentFile(encode_jpeg(image_bgr)))


def store_embedding(student, face):
    engine = get_engine()
    FaceEmbedding.objects.update_or_create(
        student=student,
        model_version=engine.model_version,
        defaults={"vector": face.embedding.astype("<f4").tobytes(), "created_at": timezone.now()},
    )
    FaceEmbedding.objects.filter(student=student).exclude(model_version=engine.model_version).delete()


def _record_consent(student, actor):
    student.consent_status = Student.Consent.GIVEN
    student.consent_recorded_at = timezone.now()
    student.consent_notice_version = settings.PRIVACY_NOTICE_VERSION
    student.consent_recorded_by = actor


def enroll_student(student, *, image_bgr, face, actor):
    """Save a new (unsaved) ``student`` with photo, embedding and consent record.

    The student starts unauthorised; an administrator must review the photo first.
    """
    new_name = _write_photo(image_bgr)
    try:
        with transaction.atomic():
            student.image.name = new_name
            student.authorized = False
            _record_consent(student, actor)
            student.save()
            store_embedding(student, face)
            AuditEvent.objects.create(
                actor=actor,
                action=AuditEvent.Action.ENROLLED,
                student=student,
                details={"privacy_notice_version": student.consent_notice_version},
            )
    except Exception:
        PrivateMediaStorage().delete(new_name)
        raise
    return student


def replace_photo(student, *, image_bgr, face, actor):
    """Replace the enrolment photo and embedding. Authorisation must be granted again."""
    new_name = _write_photo(image_bgr)
    try:
        with transaction.atomic():
            locked = Student.objects.select_for_update().get(pk=student.pk)
            old_name = locked.image.name
            locked.image.name = new_name
            locked.authorized = False
            locked.authorized_at = None
            locked.authorized_by = None
            _record_consent(locked, actor)
            locked.save()
            store_embedding(locked, face)
            AuditEvent.objects.create(
                actor=actor,
                action=AuditEvent.Action.PHOTO_REPLACED,
                student=locked,
                details={"privacy_notice_version": locked.consent_notice_version},
            )
            _delete_file_on_commit(old_name)
    except Exception:
        PrivateMediaStorage().delete(new_name)
        raise
    return locked


def update_details(student, changed_fields, *, actor):
    with transaction.atomic():
        student.save()
        AuditEvent.objects.create(
            actor=actor,
            action=AuditEvent.Action.DETAILS_CHANGED,
            student=student,
            details={"fields": sorted(changed_fields)},
        )
    return student


def set_authorization(student, authorize, *, actor):
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        if authorize:
            if not locked.has_consent:
                raise StudentStateError("Consent has not been recorded for this student.")
            if not locked.image:
                raise StudentStateError("This student has no enrolment photo.")
            if not locked.embeddings.filter(model_version=get_engine().model_version).exists():
                raise StudentStateError(
                    "No face template exists for the current model. Replace the photo or run "
                    "`manage.py rebuild_face_embeddings`."
                )
        if locked.authorized == authorize:
            return locked
        locked.authorized = authorize
        locked.authorized_at = timezone.now() if authorize else None
        locked.authorized_by = actor if authorize else None
        locked.save()
        AuditEvent.objects.create(
            actor=actor,
            action=AuditEvent.Action.AUTHORIZED if authorize else AuditEvent.Action.AUTHORIZATION_REVOKED,
            student=locked,
        )
    return locked


def remove_biometrics(student, *, actor, reason):
    """Withdraw consent: delete the photo and all face templates; keep attendance history."""
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        old_name = locked.image.name
        deleted, _ = locked.embeddings.all().delete()
        locked.image.name = ""
        locked.authorized = False
        locked.authorized_at = None
        locked.authorized_by = None
        locked.consent_status = Student.Consent.WITHDRAWN
        locked.consent_recorded_at = timezone.now()
        locked.consent_recorded_by = actor
        locked.save()
        AuditEvent.objects.create(
            actor=actor,
            action=AuditEvent.Action.BIOMETRICS_REMOVED,
            student=locked,
            reason=reason,
            details={"photo_removed": bool(old_name), "templates_removed": deleted},
        )
        _delete_file_on_commit(old_name)
    return locked


def delete_student(student, *, actor):
    """Delete a student who has no attendance history, including photo and templates."""
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        records = locked.attendance_set.count()
        if records:
            raise StudentStateError(
                f"This student has {records} attendance record(s), which are kept. Use “Remove biometric data” instead."
            )
        old_name = locked.image.name
        student_id = locked.pk
        locked.delete()
        AuditEvent.objects.create(
            actor=actor,
            action=AuditEvent.Action.STUDENT_DELETED,
            details={"student_id": student_id},
        )
        _delete_file_on_commit(old_name)
