from django.conf import settings
from django.core.checks import Error, Tags, register

from .integrations import deployment_environment


@register(Tags.security)
def analytics_export_configuration_check(app_configs, **kwargs):
    provider = getattr(settings, "TVG_ANALYTICS_EXPORT_PROVIDER", "disabled")
    backends = getattr(settings, "TVG_ANALYTICS_EXPORT_BACKENDS", {})
    findings = []
    backend = backends.get(provider)
    if not backend:
        findings.append(
            Error(
                "TVG_ANALYTICS_EXPORT_PROVIDER does not reference a configured backend.",
                id="tvg_analytics.E001",
            )
        )
        return findings
    if deployment_environment() in {"staging", "production"} and "Synthetic" in backend:
        findings.append(
            Error(
                "Synthetic analytics exporters are forbidden in staging/production.",
                id="tvg_analytics.E002",
            )
        )
    try:
        size = int(getattr(settings, "TVG_ANALYTICS_MIN_CELL_SIZE", 5))
    except (TypeError, ValueError):
        size = 0
    if size < 1:
        findings.append(
            Error(
                "TVG_ANALYTICS_MIN_CELL_SIZE must be a positive integer.",
                id="tvg_analytics.E003",
            )
        )
    return findings
