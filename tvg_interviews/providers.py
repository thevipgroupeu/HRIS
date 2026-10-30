from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


class CalendarProviderError(RuntimeError):
    """A deliberately data-free provider failure."""


@dataclass(frozen=True)
class CalendarBooking:
    reference: str
    meeting_link: str


class CalendarProvider:
    """Idempotent boundary implemented by Zoho or a later self-hosted provider."""

    def book(self, *, invitation, slot, idempotency_key):
        raise NotImplementedError

    def reschedule(self, *, booking, slot, idempotency_key):
        raise NotImplementedError

    def cancel(self, *, booking, idempotency_key):
        raise NotImplementedError


class DisabledCalendarProvider(CalendarProvider):
    def _unavailable(self):
        raise CalendarProviderError("Calendar provider is not configured.")

    def book(self, **kwargs):
        self._unavailable()

    def reschedule(self, **kwargs):
        self._unavailable()

    def cancel(self, **kwargs):
        self._unavailable()


class SimulatedCalendarProvider(CalendarProvider):
    """Deterministic no-network provider for local development and tests only."""

    @staticmethod
    def _result(key):
        token = sha256(key.encode("utf-8")).hexdigest()[:24]
        return CalendarBooking(
            reference=f"simulated-{token}",
            meeting_link=f"https://meet.example.test/interview/{token}",
        )

    def book(self, *, invitation, slot, idempotency_key):
        return self._result(idempotency_key)

    def reschedule(self, *, booking, slot, idempotency_key):
        return self._result(idempotency_key)

    def cancel(self, *, booking, idempotency_key):
        return None


def get_calendar_provider(key):
    backends = getattr(settings, "TVG_INTERVIEW_PROVIDER_BACKENDS", {})
    path = backends.get(key)
    if not path:
        raise ImproperlyConfigured(f"No calendar provider is configured for {key!r}.")
    provider = import_string(path)()
    if not isinstance(provider, CalendarProvider):
        raise ImproperlyConfigured("Calendar provider must implement CalendarProvider.")
    return provider
