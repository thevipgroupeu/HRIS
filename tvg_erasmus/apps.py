from django.apps import AppConfig


class TvgErasmusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tvg_erasmus"
    verbose_name = "THEVIPGROUP Erasmus+ Workflow"

    def ready(self):
        from tvg_erasmus import signals  # noqa: F401
