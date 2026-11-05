"""Keep the in-process embedding gallery in step with database changes."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from .models import (
    Batch,
    BatchMembership,
    FaceEmbedding,
    InstitutionSettings,
    OutboundEmail,
    StaffNotificationPreference,
    Student,
)
from .recognition.gallery import gallery


@receiver(post_save, sender=Student)
@receiver(post_delete, sender=Student)
@receiver(post_save, sender=FaceEmbedding)
@receiver(post_delete, sender=FaceEmbedding)
def invalidate_gallery(**kwargs):
    transaction.on_commit(gallery.invalidate)


# Cancel queued e-mails as soon as their recipient or permission stops being valid
# (address changed, notifications switched off, staff removed from a batch...).


def _cancel_later(**filters):
    def run():
        from .notifications.core import cancel_invalid_queued

        cancel_invalid_queued(OutboundEmail.objects.filter(status=OutboundEmail.Status.QUEUED, **filters))

    transaction.on_commit(run)


@receiver(post_save, sender=Student)
def _student_changed(instance, **kwargs):
    _cancel_later(student=instance)


@receiver(post_save, sender=StaffNotificationPreference)
def _pref_changed(instance, **kwargs):
    _cancel_later(recipient_user_id=instance.user_id)


@receiver(post_save, sender=get_user_model())
def _user_changed(instance, **kwargs):
    _cancel_later(recipient_user=instance)


@receiver(post_save, sender=InstitutionSettings)
@receiver(post_save, sender=BatchMembership)
@receiver(post_delete, sender=BatchMembership)
def _global_change(**kwargs):
    _cancel_later()


@receiver(m2m_changed, sender=Batch.instructors.through)
def _instructors_changed(**kwargs):
    _cancel_later()
