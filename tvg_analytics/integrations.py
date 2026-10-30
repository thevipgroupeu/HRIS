from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from .definitions import prohibited_tokens

REPORTING_VIEW = "tvg_analytics_reporting.metric_point_v1"


class AnalyticsExportError(RuntimeError):
    code = "analytics_export_failed"


class AnalyticsExportDisabled(AnalyticsExportError):
    code = "analytics_export_disabled"


class AnalyticsExportRejected(AnalyticsExportError):
    code = "analytics_export_rejected"


@dataclass(frozen=True)
class ExportMetric:
    dashboard: str
    metric_key: str
    period_start: str
    period_end: str
    value: Decimal | None
    numerator: Decimal | None
    denominator: Decimal | None
    dimensions: dict
    suppressed: bool = False


@dataclass(frozen=True)
class AnalyticsBatch:
    schema_version: int
    snapshot_id: str
    company_id: int
    as_of_date: str
    filters: dict
    metrics: tuple[ExportMetric, ...]

    def to_payload(self) -> dict:
        """JSON-safe contract: decimals become strings, never floats."""

        def encode(value):
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, dict):
                return {key: encode(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [encode(item) for item in value]
            return value

        return encode(asdict(self))


@dataclass(frozen=True)
class ExportResult:
    external_reference: str


def assert_batch_is_minimised(batch: AnalyticsBatch):
    """Defence in depth: refuse any identifier-bearing dimension or filter."""

    identifiers = set(batch.filters)
    for metric in batch.metrics:
        identifiers.update(metric.dimensions)
    exposed = sorted(name for name in identifiers if prohibited_tokens(name))
    if exposed:
        raise AnalyticsExportRejected(
            f"Batch contains prohibited identifiers: {', '.join(exposed)}"
        )


class DisabledAnalyticsExporter:
    destination = "disabled"

    def export(self, *, batch: AnalyticsBatch, idempotency_key: str) -> ExportResult:
        raise AnalyticsExportDisabled(
            "Analytics export is disabled until a reporting destination is approved."
        )


class ReportingViewPublisher:
    """Publishes a snapshot to the curated Metabase reporting view.

    The view reads exported snapshots straight from the PeopleOps database, so
    publication is the recorded status change itself: this backend opens no
    network connection and writes nothing outside the snapshot tables.
    """

    destination = "reporting_view"

    def export(self, *, batch: AnalyticsBatch, idempotency_key: str) -> ExportResult:
        if not idempotency_key:
            raise AnalyticsExportError("Publication requires an idempotency key.")
        assert_batch_is_minimised(batch)
        return ExportResult(external_reference=f"{REPORTING_VIEW}:{batch.snapshot_id}")


class SyntheticAnalyticsExporter:
    """Deterministic contract fake; it never opens a network connection."""

    destination = "synthetic"

    def export(self, *, batch: AnalyticsBatch, idempotency_key: str) -> ExportResult:
        if not idempotency_key or not batch.metrics:
            raise AnalyticsExportError(
                "Synthetic export requires an idempotency key and metrics."
            )
        assert_batch_is_minimised(batch)
        return ExportResult(external_reference=f"synthetic:{idempotency_key}")


def deployment_environment() -> str:
    return getattr(settings, "HORILLA_ENV", "") or getattr(
        settings, "TVG_AUTOMATION_ENVIRONMENT", "development"
    )


def get_exporter():
    provider = getattr(settings, "TVG_ANALYTICS_EXPORT_PROVIDER", "disabled")
    backends = getattr(settings, "TVG_ANALYTICS_EXPORT_BACKENDS", {})
    path = backends.get(provider)
    if not path:
        raise ImproperlyConfigured(f"Unknown analytics export provider: {provider}")
    if deployment_environment() in {"staging", "production"} and "Synthetic" in path:
        raise ImproperlyConfigured(
            "Synthetic analytics exporters are forbidden outside development/test."
        )
    return import_string(path)()
