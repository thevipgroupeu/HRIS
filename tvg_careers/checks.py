from django.conf import settings
from django.core.checks import Error, Tags, register

from horilla.settings.security import is_production_mode


@register(Tags.security)
def check_careerhub_internal_boundary(app_configs, **kwargs):
    is_prod = getattr(settings, "IS_PRODUCTION", None)
    if is_prod is None:
        is_prod = is_production_mode(
            settings.DEBUG,
            getattr(settings, "HORILLA_ENV", ""),
        )
    if not is_prod:
        return []

    errors = []
    read_enabled = getattr(settings, "CAREERHUB_INTERNAL_API_ENABLED", True)
    identity_enabled = getattr(settings, "CAREERHUB_IDENTITY_API_ENABLED", True)
    application_enabled = getattr(settings, "CAREERHUB_APPLICATION_API_ENABLED", True)
    read_token = getattr(settings, "CAREERHUB_INTERNAL_API_TOKEN", "")
    identity_token = getattr(settings, "CAREERHUB_IDENTITY_API_TOKEN", "")
    application_token = getattr(settings, "CAREERHUB_APPLICATION_API_TOKEN", "")
    if read_enabled:
        if len(read_token) < 32 or read_token.lower().startswith(("change-me", "dev-")):
            errors.append(
                Error(
                    "CAREERHUB_INTERNAL_API_TOKEN must be a strong production secret.",
                    id="tvg_careers.E001",
                )
            )
    if identity_enabled:
        if len(identity_token) < 32 or identity_token.lower().startswith(
            ("change-me", "dev-")
        ):
            errors.append(
                Error(
                    "CAREERHUB_IDENTITY_API_TOKEN must be a strong production secret.",
                    id="tvg_careers.E003",
                )
            )
        if read_enabled and read_token == identity_token:
            errors.append(
                Error(
                    "CareerHub read and identity API tokens must be different.",
                    id="tvg_careers.E004",
                )
            )
    if application_enabled:
        if len(application_token) < 32 or application_token.lower().startswith(
            ("change-me", "dev-")
        ):
            errors.append(
                Error(
                    "CAREERHUB_APPLICATION_API_TOKEN must be a strong production secret.",
                    id="tvg_careers.E005",
                )
            )
        other_tokens = {
            token
            for enabled, token in (
                (read_enabled, read_token),
                (identity_enabled, identity_token),
            )
            if enabled
        }
        if application_token in other_tokens:
            errors.append(
                Error(
                    "CareerHub application API token must be distinct.",
                    id="tvg_careers.E006",
                )
            )
    if (read_enabled or identity_enabled or application_enabled) and not getattr(
        settings, "TVG_LEGAL_EMPLOYER_ID", None
    ):
        errors.append(
            Error(
                "TVG_LEGAL_EMPLOYER_ID is required in production.",
                id="tvg_careers.E002",
            )
        )
    return errors
