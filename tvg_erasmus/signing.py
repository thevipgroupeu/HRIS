"""Adapter boundary for an explicitly configured self-hosted signing service."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils.module_loading import import_string


class DisabledSigningProvider:
    def request_signature(self, **kwargs):
        raise ValidationError(
            "The signing integration is disabled; use the manual signed-upload fallback."
        )


class TestSigningProvider:
    """No-network provider for automated tests only."""

    def request_signature(self, *, idempotency_key, **kwargs):
        return f"synthetic:{idempotency_key}"


def get_signing_provider(provider_key):
    backends = getattr(
        settings,
        "TVG_ERASMUS_SIGNING_BACKENDS",
        {"self_hosted": "tvg_erasmus.signing.DisabledSigningProvider"},
    )
    path = backends.get(provider_key)
    if not path:
        raise ImproperlyConfigured("Unknown Erasmus signing provider.")
    return import_string(path)()
