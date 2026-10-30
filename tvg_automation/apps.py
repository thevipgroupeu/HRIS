from django.apps import AppConfig


class TvgAutomationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tvg_automation"
    verbose_name = "THEVIPGROUP Workflow Automation"

    def ready(self):
        from . import checks  # noqa: F401
