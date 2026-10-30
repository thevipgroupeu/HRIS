"""Synthetic PostgreSQL/Redis settings for release integration checks only.

Unlike ``tvg_test``, this module deliberately retains the database and cache
configured by ``DATABASE_URL`` and ``REDIS_URL`` and enables migrations. It is
not selected by any WSGI/ASGI entry point. External effects remain local or
synthetic, and test runners must provide isolated disposable services.
"""

import os

from . import *  # noqa: F403

DEBUG = False
HORILLA_ENV = "test"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MEDIA_ROOT = os.getenv("TVG_SYNTHETIC_MEDIA_ROOT", "/tmp/tvg-pg-test-media")
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": MEDIA_ROOT},
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}
for _name in (
    "VERIFICATION",
    "ACCEPTANCE",
    "ERASMUS_DOCUMENT",
    "ONBOARDING",
    "REPORTING",
    "OFFBOARDING",
):
    globals()[f"TVG_{_name}_PRIVATE_MEDIA_ROOT"] = MEDIA_ROOT

TVG_AUTOMATION_ENABLED = True
TVG_AUTOMATION_ENVIRONMENT = "test"
TVG_ACTIVEPIECES_WEBHOOK_URL = "https://activepieces.invalid/hooks/synthetic"
TVG_ACTIVEPIECES_OUTBOUND_SIGNING_SECRET = (
    "synthetic-outbound-signing-secret-000000000000"
)
TVG_ACTIVEPIECES_CALLBACK_SIGNING_SECRET = (
    "synthetic-callback-signing-secret-000000000000"
)
TVG_AUTOMATION_TRANSPORT_BACKEND = (
    "tvg_automation.transport.SyntheticActivepiecesTransport"
)
TVG_TRAINING_PROVIDER = "synthetic"
TVG_TRAINING_PROVIDER_BACKENDS = {
    "disabled": "tvg_training.integrations.DisabledTrainingProvider",
    "synthetic": "tvg_training.integrations.SyntheticTrainingProvider",
}
TVG_ANALYTICS_EXPORT_PROVIDER = "synthetic"
TVG_ANALYTICS_EXPORT_BACKENDS = {
    "disabled": "tvg_analytics.integrations.DisabledAnalyticsExporter",
    "reporting_view": "tvg_analytics.integrations.ReportingViewPublisher",
    "synthetic": "tvg_analytics.integrations.SyntheticAnalyticsExporter",
}
TVG_ANALYTICS_MIN_CELL_SIZE = 5
TVG_RECRUITMENT_FROM_EMAIL = "recruitment@thevipgroup.eu"
TVG_RECRUITMENT_REPLY_TO = "recruitment@thevipgroup.eu"
TVG_INTERVIEW_PROVIDER_BACKENDS = {
    "simulated": "tvg_interviews.providers.SimulatedCalendarProvider",
    "zoho_bookings": "tvg_interviews.providers.DisabledCalendarProvider",
    "self_hosted": "tvg_interviews.providers.DisabledCalendarProvider",
}
TVG_ONBOARDING_PROVISIONING_BACKENDS = {
    "manual": "tvg_onboarding.integrations.DisabledProvisioningProvider",
    "zoho": "tvg_onboarding.integrations.DisabledProvisioningProvider",
    "self_hosted": "tvg_onboarding.integrations.DisabledProvisioningProvider",
    "synthetic": "tvg_onboarding.integrations.TestProvisioningProvider",
}
TVG_REPORTING_AI_PROVIDER = "synthetic"
TVG_OFFBOARDING_REVOCATION_BACKENDS = {
    "manual": "tvg_offboarding.integrations.DisabledAccessRevocationProvider",
    "synthetic": "tvg_offboarding.integrations.SyntheticAccessRevocationProvider",
}
