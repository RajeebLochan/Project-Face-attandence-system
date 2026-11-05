from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync_roles(sender, **kwargs):
    from .roles import sync_roles

    sync_roles()


class App1Config(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app1"
    verbose_name = "Attendance"

    def ready(self):
        from . import signals  # noqa: F401  (connect signal handlers)

        post_migrate.connect(_sync_roles, sender=self, dispatch_uid="app1-sync-roles")
