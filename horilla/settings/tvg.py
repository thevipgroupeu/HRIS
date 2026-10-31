"""Shipped THEVIPGROUP configuration, independent of local-only overrides.

This module must be included in release images. Secrets are read only from the
environment. The public CareerHub runtime deliberately does not import it.
"""

import os

from .base import BASE_DIR, INSTALLED_APPS, MIDDLEWARE

# Product-facing identity. Internal module names retain their upstream names so
# updates and migrations remain compatible, but no end-user surface should use
# the upstream product brand.
TVG_BRAND_NAME = "THEVIPGROUP"
TVG_PRODUCT_NAME = "THEVIPGROUP PeopleOps"
TVG_BRAND_LOGO_STATIC_PATH = (
    "horilla_theme/assets/img/branding/thevipgroup-logo.png"
)

# THEVIPGROUP-owned extensions are registered here, after upstream Horilla
# apps, so the fork can continue to merge upstream settings with minimal
# conflicts. Keep dependency order: careers references the core identity and
# venture models.
INSTALLED_APPS += [
    "tvg_core.apps.TvgCoreConfig",
    "tvg_careers.apps.TvgCareersConfig",
    "tvg_erasmus.apps.TvgErasmusConfig",
    "tvg_interviews.apps.TvgInterviewsConfig",
    "tvg_onboarding.apps.TvgOnboardingConfig",
    "tvg_peoplehub.apps.TvgPeopleHubConfig",
    "tvg_reporting.apps.TvgReportingConfig",
    "tvg_offboarding.apps.TvgOffboardingConfig",
    "tvg_automation.apps.TvgAutomationConfig",
    "tvg_training.apps.TvgTrainingConfig",
    "tvg_analytics.apps.TvgAnalyticsConfig",
]

# Apply the analytics staff boundary after AuthenticationMiddleware has loaded
# request.user but before Horilla's CompanyMiddleware converts a missing
# Employee link into a logout redirect.
MIDDLEWARE.insert(
    MIDDLEWARE.index("base.middleware.CompanyMiddleware"),
    "tvg_analytics.middleware.AnalyticsEmployeeBoundaryMiddleware",
)

# The private PeopleOps process mounts Horilla plus a narrow, authenticated,
# read-only API for the separately deployed public CareerHub process.
ROOT_URLCONF = "tvg_careers.peopleops_urls"
TVG_AUTOMATION_ENABLED = os.getenv("TVG_AUTOMATION_ENABLED", "0").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TVG_AUTOMATION_ENVIRONMENT = os.getenv("TVG_AUTOMATION_ENVIRONMENT", "development")
TVG_ACTIVEPIECES_WEBHOOK_URL = os.getenv("TVG_ACTIVEPIECES_WEBHOOK_URL", "")
TVG_ACTIVEPIECES_OUTBOUND_SIGNING_SECRET = os.getenv(
    "TVG_ACTIVEPIECES_OUTBOUND_SIGNING_SECRET", ""
)
TVG_ACTIVEPIECES_CALLBACK_SIGNING_SECRET = os.getenv(
    "TVG_ACTIVEPIECES_CALLBACK_SIGNING_SECRET", ""
)
TVG_AUTOMATION_TRANSPORT_BACKEND = os.getenv(
    "TVG_AUTOMATION_TRANSPORT_BACKEND",
    "tvg_automation.transport.DisabledActivepiecesTransport",
)
TVG_AUTOMATION_MAX_ATTEMPTS = int(os.getenv("TVG_AUTOMATION_MAX_ATTEMPTS", "5"))
TVG_AUTOMATION_CALLBACK_MAX_SKEW_SECONDS = int(
    os.getenv("TVG_AUTOMATION_CALLBACK_MAX_SKEW_SECONDS", "300")
)
TVG_TRAINING_PROVIDER = os.getenv("TVG_TRAINING_PROVIDER", "disabled")
TVG_TRAINING_PROVIDER_BACKENDS = {
    "disabled": "tvg_training.integrations.DisabledTrainingProvider",
    "activepieces": os.getenv(
        "TVG_TRAINING_ACTIVEPIECES_BACKEND",
        "tvg_training.integrations.DisabledTrainingProvider",
    ),
}
# Analytics publication is opt-in: enable "reporting_view" only after the DBA
# has created the Metabase reader role (docs/integrations/METABASE_REPORTING.md).
TVG_ANALYTICS_EXPORT_PROVIDER = os.getenv("TVG_ANALYTICS_EXPORT_PROVIDER", "disabled")
TVG_ANALYTICS_EXPORT_BACKENDS = {
    "disabled": "tvg_analytics.integrations.DisabledAnalyticsExporter",
    "reporting_view": "tvg_analytics.integrations.ReportingViewPublisher",
}
TVG_ANALYTICS_MIN_CELL_SIZE = int(os.getenv("TVG_ANALYTICS_MIN_CELL_SIZE", "5"))
TVG_RECRUITMENT_FROM_EMAIL = os.getenv(
    "TVG_RECRUITMENT_FROM_EMAIL", "recruitment@thevipgroup.eu"
)
TVG_RECRUITMENT_REPLY_TO = os.getenv(
    "TVG_RECRUITMENT_REPLY_TO", "recruitment@thevipgroup.eu"
)
CAREERHUB_INTERNAL_API_TOKEN = os.getenv("CAREERHUB_INTERNAL_API_TOKEN", "")
CAREERHUB_INTERNAL_API_ENABLED = os.getenv(
    "CAREERHUB_INTERNAL_API_ENABLED", "1"
).lower() in {"1", "true", "yes", "on"}
CAREERHUB_IDENTITY_API_TOKEN = os.getenv("CAREERHUB_IDENTITY_API_TOKEN", "")
CAREERHUB_IDENTITY_API_ENABLED = os.getenv(
    "CAREERHUB_IDENTITY_API_ENABLED", "1"
).lower() in {"1", "true", "yes", "on"}
CAREERHUB_APPLICATION_API_TOKEN = os.getenv("CAREERHUB_APPLICATION_API_TOKEN", "")
CAREERHUB_APPLICATION_API_ENABLED = os.getenv(
    "CAREERHUB_APPLICATION_API_ENABLED", "1"
).lower() in {"1", "true", "yes", "on"}
CAREERHUB_VERIFICATION_API_TOKEN = os.getenv("CAREERHUB_VERIFICATION_API_TOKEN", "")
CAREERHUB_VERIFICATION_API_ENABLED = os.getenv(
    "CAREERHUB_VERIFICATION_API_ENABLED", "1"
).lower() in {"1", "true", "yes", "on"}
CAREERHUB_INTERVIEW_API_TOKEN = os.getenv("CAREERHUB_INTERVIEW_API_TOKEN", "")
CAREERHUB_INTERVIEW_API_ENABLED = os.getenv(
    "CAREERHUB_INTERVIEW_API_ENABLED", "1"
).lower() in {"1", "true", "yes", "on"}
CAREERHUB_ARRANGEMENT_API_TOKEN = os.getenv("CAREERHUB_ARRANGEMENT_API_TOKEN", "")
CAREERHUB_ARRANGEMENT_API_ENABLED = os.getenv(
    "CAREERHUB_ARRANGEMENT_API_ENABLED", "1"
).lower() in {"1", "true", "yes", "on"}
TVG_INTERVIEW_PROVIDER_BACKENDS = {
    "simulated": "tvg_interviews.providers.DisabledCalendarProvider",
    "zoho_bookings": "tvg_interviews.providers.DisabledCalendarProvider",
    "self_hosted": "tvg_interviews.providers.DisabledCalendarProvider",
}
TVG_VERIFICATION_PRIVATE_MEDIA_ROOT = os.getenv(
    "TVG_VERIFICATION_PRIVATE_MEDIA_ROOT",
    os.path.join(BASE_DIR, "verification-private-media"),
)
TVG_ACCEPTANCE_PRIVATE_MEDIA_ROOT = os.getenv(
    "TVG_ACCEPTANCE_PRIVATE_MEDIA_ROOT",
    os.path.join(BASE_DIR, "acceptance-private-media"),
)
TVG_ERASMUS_DOCUMENT_PRIVATE_MEDIA_ROOT = os.getenv(
    "TVG_ERASMUS_DOCUMENT_PRIVATE_MEDIA_ROOT",
    os.path.join(BASE_DIR, "erasmus-document-private-media"),
)
TVG_ONBOARDING_PRIVATE_MEDIA_ROOT = os.getenv(
    "TVG_ONBOARDING_PRIVATE_MEDIA_ROOT",
    os.path.join(BASE_DIR, "onboarding-private-media"),
)
TVG_REPORTING_PRIVATE_MEDIA_ROOT = os.getenv(
    "TVG_REPORTING_PRIVATE_MEDIA_ROOT",
    os.path.join(BASE_DIR, "reporting-private-media"),
)
TVG_OFFBOARDING_PRIVATE_MEDIA_ROOT = os.getenv(
    "TVG_OFFBOARDING_PRIVATE_MEDIA_ROOT",
    os.path.join(BASE_DIR, "offboarding-private-media"),
)
TVG_OFFBOARDING_REVOCATION_BACKENDS = {
    "manual": "tvg_offboarding.integrations.DisabledAccessRevocationProvider",
    "synthetic": "tvg_offboarding.integrations.DisabledAccessRevocationProvider",
}
TVG_REPORTING_AI_PROVIDER = os.getenv("TVG_REPORTING_AI_PROVIDER", "disabled")
TVG_REPORTING_AI_BACKENDS = {
    "disabled": "tvg_reporting.ai.DisabledReportingAIProvider",
    # The synthetic backend is deterministic and is available only when a
    # test settings module explicitly selects it.
    "synthetic": "tvg_reporting.ai.SyntheticReportingAIProvider",
}
TVG_ONBOARDING_PROVISIONING_BACKENDS = {
    "manual": "tvg_onboarding.integrations.DisabledProvisioningProvider",
    "zoho": os.getenv(
        "TVG_ONBOARDING_ZOHO_BACKEND",
        "tvg_onboarding.integrations.DisabledProvisioningProvider",
    ),
    "self_hosted": os.getenv(
        "TVG_ONBOARDING_SELF_HOSTED_BACKEND",
        "tvg_onboarding.integrations.DisabledProvisioningProvider",
    ),
}
TVG_PEOPLEHUB_SSO_BACKENDS = {
    "disabled": "tvg_peoplehub.integrations.DisabledAuthentikProvider",
    "authentik": os.getenv(
        "TVG_PEOPLEHUB_AUTHENTIK_BACKEND",
        "tvg_peoplehub.integrations.DisabledAuthentikProvider",
    ),
}
TVG_PEOPLEHUB_SSO_PROVIDER = os.getenv("TVG_PEOPLEHUB_SSO_PROVIDER", "disabled")
TVG_ERASMUS_SIGNING_BACKENDS = {
    "self_hosted": os.getenv(
        "TVG_ERASMUS_SELF_HOSTED_SIGNING_BACKEND",
        "tvg_erasmus.signing.DisabledSigningProvider",
    )
}
TVG_ERASMUS_EXTERNAL_REQUIREMENT_BACKENDS = {
    "na10": os.getenv(
        "TVG_ERASMUS_NA10_BACKEND",
        "tvg_erasmus.integrations.DisabledExternalRequirementProvider",
    )
}
TVG_VERIFICATION_SCANNER_BACKEND = os.getenv(
    "TVG_VERIFICATION_SCANNER_BACKEND",
    "tvg_erasmus.scanning.DisabledScanner",
)
try:
    TVG_LEGAL_EMPLOYER_ID = int(os.getenv("TVG_LEGAL_EMPLOYER_ID", ""))
except ValueError:
    TVG_LEGAL_EMPLOYER_ID = None

# Narrow upstream hooks: never use a logged-in session alone as file authority.
HORILLA_PRIVATE_MEDIA_AUTHORIZER = "tvg_core.access.can_download_media"
HORILLA_PUBLIC_MEDIA_PREFIXES = ("base/icon/", "base/company/icon/")
HORILLA_RECRUITMENT_ACCESS_SCOPE = "tvg_core.access.recruitment_scope"
