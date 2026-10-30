from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TvgCareersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tvg_careers"
    verbose_name = _("THEVIPGROUP Careers")

    def ready(self):
        from tvg_careers import checks  # noqa: F401
