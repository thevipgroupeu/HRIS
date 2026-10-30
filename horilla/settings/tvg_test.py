"""Explicit local-only fast settings for THEVIPGROUP extension tests.

This module is never selected by a runtime WSGI/ASGI entry point.  It uses an
in-memory SQLite database and builds the current model state directly so the
large upstream migration history does not dominate focused extension tests.
Migration consistency is checked separately with ``makemigrations --check``.
"""

from . import *  # noqa: F403


class _DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
MIGRATION_MODULES = _DisableMigrations()
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEBUG = False
HORILLA_ENV = "test"
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
