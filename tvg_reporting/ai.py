from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


class AIUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AIResult:
    summary: str
    missing_evidence: tuple[str, ...]
    supervisor_briefing: str
    model_version: str


class ReportingAIProvider(Protocol):
    provider_name: str

    def analyze(self, *, content: dict, purpose: str) -> AIResult: ...


class DisabledReportingAIProvider:
    provider_name = "disabled"

    def analyze(self, *, content: dict, purpose: str) -> AIResult:
        raise AIUnavailable("Reporting AI support is not configured.")


class SyntheticReportingAIProvider:
    """Deterministic test provider; never assigns performance outcomes."""

    provider_name = "synthetic"

    def analyze(self, *, content: dict, purpose: str) -> AIResult:
        missing = tuple(
            label
            for key, label in (
                ("evidence_links", "evidence links"),
                ("completed_tasks", "completed tasks"),
            )
            if not content.get(key)
        )
        return AIResult(
            summary="Synthetic summary for supervisor review.",
            missing_evidence=missing,
            supervisor_briefing=(
                "Synthetic briefing. Human review is required; no performance "
                "decision has been made."
            ),
            model_version="synthetic-v1",
        )


def get_reporting_ai_provider() -> ReportingAIProvider:
    provider = getattr(settings, "TVG_REPORTING_AI_PROVIDER", "disabled")
    backends = getattr(settings, "TVG_REPORTING_AI_BACKENDS", {})
    path = backends.get(provider)
    if not path:
        raise ImproperlyConfigured("Unknown reporting AI provider.")
    return import_string(path)()
