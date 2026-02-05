from django.apps import AppConfig


class RifasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = 'rifas'

    def ready(self):
        from . import signals  # noqa: F401
