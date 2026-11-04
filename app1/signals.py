"""Keep the in-process embedding gallery in step with database changes."""
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import FaceEmbedding, Student
from .recognition.gallery import gallery


@receiver(post_save, sender=Student)
@receiver(post_delete, sender=Student)
@receiver(post_save, sender=FaceEmbedding)
@receiver(post_delete, sender=FaceEmbedding)
def invalidate_gallery(**kwargs):
    transaction.on_commit(gallery.invalidate)
