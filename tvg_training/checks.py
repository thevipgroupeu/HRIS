from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security)
def training_integration_configuration_check(app_configs, **kwargs):
    provider = getattr(settings, "TVG_TRAINING_PROVIDER", "disabled")
    backends = getattr(settings, "TVG_TRAINING_PROVIDER_BACKENDS", {})
    environment = getattr(settings, "HORILLA_ENV", "") or getattr(
        settings, "TVG_AUTOMATION_ENVIRONMENT", "development"
    )
    findings = []
    backend = backends.get(provider)
    if not backend:
        findings.append(
            Error(
                "TVG_TRAINING_PROVIDER does not reference a configured backend.",
                id="tvg_training.E001",
            )
        )
        return findings
    if environment in {"staging", "production"} and "Synthetic" in backend:
        findings.append(
            Error(
                "Synthetic TrainingHub providers are forbidden in staging/production.",
                id="tvg_training.E002",
            )
        )
    if provider == "activepieces" and "DisabledTrainingProvider" in backend:
        findings.append(
            Error(
                "The Activepieces training provider is selected but not implemented.",
                id="tvg_training.E003",
            )
        )
    return findings
