from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class RevocationResult:
    supported: bool
    revoked: bool = False
    proof_reference: str = ""
    message: str = ""


class DisabledAccessRevocationProvider:
    """Fail closed when no authorized provider API contract exists."""

    provider_name = "manual"

    def revoke(self, *, task, employee):
        return RevocationResult(
            supported=False,
            message=(
                "No authorized revocation API is configured. Complete the tracked "
                "manual IT task and record non-secret proof."
            ),
        )


class SyntheticAccessRevocationProvider:
    """Deterministic test-only provider; performs no external side effect."""

    provider_name = "synthetic"

    def revoke(self, *, task, employee):
        return RevocationResult(
            supported=True,
            revoked=True,
            proof_reference=f"synthetic-revocation-{task.idempotency_key}",
        )


def get_access_revocation_provider(key):
    backends = getattr(settings, "TVG_OFFBOARDING_REVOCATION_BACKENDS", {})
    path = backends.get(key)
    if not path:
        raise ImproperlyConfigured("Unknown access-revocation provider.")
    return import_string(path)()
