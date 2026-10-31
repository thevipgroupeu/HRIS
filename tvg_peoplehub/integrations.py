from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class SSOAvailability:
    available: bool
    reason: str


class SSOProvider(Protocol):
    """Narrow future boundary; authentication does not depend on it yet."""

    def availability(self) -> SSOAvailability: ...


class DisabledAuthentikProvider:
    """Safe default until an approved authentik deployment is configured."""

    def availability(self) -> SSOAvailability:
        return SSOAvailability(
            available=False,
            reason="External SSO is not configured; local PeopleHub authentication is active.",
        )


def get_sso_provider() -> SSOProvider:
    """Resolve the configured adapter; this does not initiate an SSO exchange."""
    provider = getattr(settings, "TVG_PEOPLEHUB_SSO_PROVIDER", "disabled")
    backends = getattr(settings, "TVG_PEOPLEHUB_SSO_BACKENDS", {})
    path = backends.get(provider)
    if not path:
        raise ImproperlyConfigured("Unknown PeopleHub SSO provider.")
    return import_string(path)()
