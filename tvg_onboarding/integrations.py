from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class ProvisioningResult:
    supported: bool
    completed: bool = False
    email: str = ""
    external_reference: str = ""
    message: str = ""


class DisabledProvisioningProvider:
    """Fail-closed boundary for providers without an authorized mailbox API."""

    def provision(self, *, request, onboarding):
        return ProvisioningResult(
            supported=False,
            message=(
                "No authorized provisioning API is configured. Complete the tracked "
                "manual IT task."
            ),
        )


class TestProvisioningProvider:
    """Deterministic provider available only when tests explicitly configure it."""

    def provision(self, *, request, onboarding):
        if request.requested_email:
            email = request.requested_email
        else:
            raise ImproperlyConfigured("Synthetic provider requires requested_email.")
        return ProvisioningResult(
            supported=True,
            completed=True,
            email=email,
            external_reference=f"synthetic-{request.idempotency_key}",
        )
