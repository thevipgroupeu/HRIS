from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TvgCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tvg_core"
    verbose_name = _("THEVIPGROUP Core")
