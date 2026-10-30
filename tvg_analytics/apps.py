from django.apps import AppConfig, apps


class TvgAnalyticsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tvg_analytics"
    verbose_name = "THEVIPGROUP PeopleOps analytics"

    def ready(self):
        from . import checks  # noqa: F401

        if apps.is_installed("report"):
            from .reports import register_reports

            register_reports()
