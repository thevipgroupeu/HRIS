from django.apps import AppConfig


class TvgTrainingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tvg_training"
    verbose_name = "THEVIPGROUP TrainingHub Integration"

    def ready(self):
        from . import checks  # noqa: F401
